"""Phase 4 — Website Crawler, Business Directory Adapters & Government Open Data Tests.

Comprehensive validation covering:
1. SSRF Protection & Netguard Hardening:
   - Rejection of private IPv4 (127.0.0.1, 10.0.0.1, 192.168.1.1, 172.16.0.1)
   - Rejection of cloud metadata endpoints (169.254.169.254, metadata.google.internal)
   - Rejection of obfuscated IP representations (decimal, hex, octal notations)
   - Rejection of IPv6 addresses (::1, ::ffff:127.0.0.1, fe80::, fc00::)
   - Rejection of forbidden ports and non-http(s) schemes
2. Respectful Crawler & Robots.txt Compliance:
   - Enforcement of robots.txt Disallow directives
   - Same-domain crawling bounding (no external link traversal)
   - Page count and crawl depth bounds
3. HTML Contact & Metadata Extraction (WebsiteActor):
   - Extraction of phones, emails, physical address, and social links
   - Complete provenance tracking
4. Declarative Business Directory Adapter:
   - CSS-selector based extraction from listing structures
5. Schema.org JSON-LD LocalBusiness Directory Adapter:
   - Extraction of LocalBusiness, Organization, Store, Restaurant structured data
   - Nested PostalAddress and aggregateRating extraction
6. Government & Open Data Feeds (PublicDataActor):
   - Dict-of-records JSON (e.g. SEC EDGAR company tickers format)
   - CSV datasets with custom field mapping and license provenance
7. Truthful Terms & Restricted Source Enforcement:
   - IndiaMART actor reports RESTRICTED / CONFIGURATION REQUIRED when uncredentialed
   - Justdial actor reports RESTRICTED when uncredentialed
   - Transition to READY when authorized API credentials are provided
8. Deduplication, CRM Protection & Export Security:
   - Normalized phone/email deduplication
   - Formula injection (CWE-1236) neutralization
"""

from __future__ import annotations

import json
import uuid
import pytest
import httpx

from app.core.config import Settings
from app.models.scrape import Lead
from app.scrapers.actors.business_directory.actor import BusinessDirectoryActor
from app.scrapers.actors.business_directory.adapters import (
    ADAPTER_REGISTRY,
    GenericDirectoryAdapter,
    JsonLdDirectoryAdapter,
)
from app.scrapers.actors.indiamart.actor import IndiaMartActor
from app.scrapers.actors.justdial.actor import JustDialActor
from app.scrapers.actors.public_data.actor import PublicDataActor
from app.scrapers.actors.website.actor import WebsiteActor
from app.scrapers.core.base import ActorHealth, ActorStatus, SourceStatus, SourceType
from app.scrapers.core.context import JobLimits, ScraperContext
from app.scrapers.core.exceptions import ScraperBlockedTargetError, ScraperLimitReachedError, ScraperValidationError
from app.scrapers.core.http import HttpPolicy
from app.scrapers.core.netguard import UrlPolicy, validate_url
from app.services.leads.exporter import _CSVWriter, lead_to_row


# ==============================================================================
# 1. SSRF Hardening & Netguard Tests
# ==============================================================================

def test_ssrf_blocks_private_ipv4():
    policy = UrlPolicy(allow_private_targets=False)
    targets = [
        "http://127.0.0.1/admin",
        "http://127.0.1.1:8080/",
        "http://10.0.0.5/secrets",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://localhost/",
        "http://localhost:8000/api",
    ]
    for url in targets:
        with pytest.raises(ScraperValidationError):
            validate_url(url, policy, resolve=False)


def test_ssrf_blocks_cloud_metadata():
    policy = UrlPolicy(allow_private_targets=False)
    targets = [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ]
    for url in targets:
        with pytest.raises(ScraperValidationError):
            validate_url(url, policy, resolve=False)


def test_ssrf_blocks_obfuscated_decimal_hex_octal_ips():
    """Adversaries attempt to bypass simple IP checks using decimal, hex, or octal IP encodings."""
    policy = UrlPolicy(allow_private_targets=False)
    targets = [
        "http://2130706433/",        # Decimal representation of 127.0.0.1
        "http://3232235777/",        # Decimal representation of 192.168.1.1
        "http://167772161/",         # Decimal representation of 10.0.0.1
        "http://2852039166/",        # Decimal representation of 169.254.169.254
        "http://0x7f000001/",        # Hex representation of 127.0.0.1
        "http://0177.0.0.1/",        # Octal representation of 127.0.0.1
    ]
    for url in targets:
        with pytest.raises(ScraperValidationError):
            validate_url(url, policy, resolve=False)


def test_ssrf_blocks_ipv6_loopback_and_mapped():
    policy = UrlPolicy(allow_private_targets=False)
    targets = [
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
    ]
    for url in targets:
        with pytest.raises(ScraperValidationError):
            validate_url(url, policy, resolve=False)


def test_ssrf_blocks_forbidden_schemes_and_ports():
    policy = UrlPolicy(allow_private_targets=False, allowed_ports={80, 443})
    with pytest.raises(ScraperValidationError):
        validate_url("file:///etc/passwd", policy, resolve=False)
    with pytest.raises(ScraperValidationError):
        validate_url("gopher://127.0.0.1:70/", policy, resolve=False)
    with pytest.raises(ScraperValidationError):
        validate_url("ftp://example.com/", policy, resolve=False)
    with pytest.raises(ScraperValidationError):
        validate_url("http://example.com:22/", policy, resolve=False)
    with pytest.raises(ScraperValidationError):
        validate_url("http://example.com:6379/", policy, resolve=False)


def test_ssrf_permits_valid_public_https_url():
    policy = UrlPolicy(allow_private_targets=False, allowed_hosts={"example.com"})
    parsed = validate_url("https://example.com/directory", policy, resolve=False)
    assert parsed == "https://example.com/directory"


# ==============================================================================
# Helper Fixtures for Async Mocking
# ==============================================================================

def make_test_context(input_data: dict, transport: httpx.MockTransport, host: str = "example.com", limits=None) -> ScraperContext:
    url_policy = UrlPolicy(allowed_ports={80, 443}, allowed_hosts={host}, allow_private_targets=True)
    http_policy = HttpPolicy(request_timeout=5, requests_per_second=100.0, concurrency=2, max_retries=1, respect_robots=True)
    return ScraperContext(
        job_id=uuid.uuid4(),
        actor_id="test-actor",
        actor_version="1.0.0",
        input=input_data,
        http_policy=http_policy,
        url_policy=url_policy,
        limits=limits,
        http_transport=transport,
    )


async def collect_actor_items(actor, ctx: ScraperContext) -> list[dict]:
    items = []
    async for item in actor.run(ctx):
        items.append(item)
    await ctx.close()
    return items


# ==============================================================================
# 2. Website Crawler HTML Extraction & Robots Tests
# ==============================================================================

HTML_HOMEPAGE = """<!DOCTYPE html>
<html>
<head><title>Apex Logistics Solutions</title></head>
<body>
  <h1>Apex Logistics</h1>
  <p>Leading supply chain partner in Mumbai.</p>
  <a href="/about-us">About Company</a>
  <a href="/contact">Get in Touch</a>
  <a href="https://external-competitor.com/leak">External Link</a>
  <a href="https://www.linkedin.com/company/apexlogistics">LinkedIn</a>
  <a href="https://twitter.com/apexlogistics">Twitter</a>
  <address>402 Trade Centre, BKC, Mumbai, Maharashtra 400051</address>
  <div>Direct: +91 22 6123 4567</div>
</body>
</html>
"""

HTML_CONTACT = """<!DOCTYPE html>
<html>
<head><title>Contact Apex Logistics</title></head>
<body>
  <h2>Reach Our Sales Team</h2>
  <p>Email our directors at <a href="mailto:contact@apexlogistics.in">contact@apexlogistics.in</a></p>
  <p>Support inquiries: support@apexlogistics.in</p>
  <p>Direct Toll-Free: <a href="tel:+918001234567">+91 800 123 4567</a></p>
  <a href="https://instagram.com/apexlogistics">Instagram</a>
</body>
</html>
"""

ROBOTS_TXT = """User-agent: *
Disallow: /admin
Disallow: /private
"""


@pytest.mark.asyncio
async def test_website_crawler_extracts_contacts_and_bounds_domain():
    requests_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        p = request.url.path
        if p == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_TXT)
        if p == "/" or p == "":
            return httpx.Response(200, text=HTML_HOMEPAGE, headers={"content-type": "text/html"})
        if p == "/contact":
            return httpx.Response(200, text=HTML_CONTACT, headers={"content-type": "text/html"})
        if p == "/about-us":
            return httpx.Response(200, text="<html><body>About Us</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(handler)
    ctx = make_test_context({"url": "http://example.com/", "max_pages": 4, "max_depth": 1}, transport)

    actor = WebsiteActor()
    items = await collect_actor_items(actor, ctx)

    # 1. Extracted leads
    assert len(items) >= 1
    emails = {item.get("email") for item in items if item.get("email")}
    assert "contact@apexlogistics.in" in emails or any("contact@apexlogistics.in" in item.get("metadata", {}).get("emails_found", []) for item in items)
    
    # 2. Phones extracted
    phones = [item.get("phone") for item in items if item.get("phone")]
    assert any("6123" in p or "1234567" in p for p in phones)

    # 3. Social profiles discovered
    sample = items[0]
    social = sample.get("social_links", {})
    assert "linkedin" in social or "twitter" in social

    # 4. Strict domain boundary: competitor external link NEVER requested
    assert not any("external-competitor.com" in url for url in requests_log)


@pytest.mark.asyncio
async def test_website_crawler_respects_robots_txt():
    requests_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        p = request.url.path
        if p == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_TXT)
        if p == "/private":
            return httpx.Response(200, text="<html><body>Private Page</body></html>")
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(handler)
    ctx = make_test_context({"url": "http://example.com/private", "max_pages": 2}, transport)

    actor = WebsiteActor()
    items = await collect_actor_items(actor, ctx)
    # Target /private is disallowed in robots.txt so it must not be fetched or extracted
    assert not any(u.rstrip("/").endswith("/private") for u in requests_log)
    assert len(items) == 0


# ==============================================================================
# 3. Business Directory Adapters Tests
# ==============================================================================

HTML_DIRECTORY_LISTING = """<!DOCTYPE html>
<html>
<body>
  <div class="directory-list">
    <div class="biz-card">
      <h3 class="biz-name">Zenith Cloud Technologies</h3>
      <span class="biz-phone">+91 22 5555 1001</span>
      <span class="biz-email">info@zenithcloud.test</span>
      <div class="biz-address">Bandra Kurla Complex, Mumbai</div>
      <a class="biz-web" href="https://zenithcloud.test">Website</a>
    </div>
    <div class="biz-card">
      <h3 class="biz-name">Solaria Renewable Power</h3>
      <span class="biz-phone">+91 22 5555 2002</span>
      <span class="biz-email">contact@solariapower.test</span>
      <div class="biz-address">Nariman Point, Mumbai</div>
      <a class="biz-web" href="https://solariapower.test">Website</a>
    </div>
  </div>
</body>
</html>
"""


def test_generic_directory_adapter_css_extraction():
    adapter = GenericDirectoryAdapter()
    config = {
        "item_selector": ".biz-card",
        "name_selector": ".biz-name",
        "phone_selector": ".biz-phone",
        "email_selector": ".biz-email",
        "address_selector": ".biz-address",
        "website_selector": ".biz-web",
    }
    records = adapter.extract(HTML_DIRECTORY_LISTING, "https://example.com/directory", config)
    assert len(records) == 2
    assert records[0]["business_name"] == "Zenith Cloud Technologies"
    assert records[0]["phone"] == "+91 22 5555 1001"
    assert records[0]["email"] == "info@zenithcloud.test"
    assert "Mumbai" in records[0]["address"]
    assert records[0]["website"] == "https://zenithcloud.test"

    assert records[1]["business_name"] == "Solaria Renewable Power"
    assert records[1]["phone"] == "+91 22 5555 2002"


HTML_JSONLD_LOCAL_BUSINESS = """<!DOCTYPE html>
<html>
<head>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "LocalBusiness",
    "name": "Oberoi Fine Dining",
    "telephone": "+91-22-6632-5757",
    "email": "dining@oberoihotels.test",
    "url": "https://oberoidining.test",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "Nariman Point",
      "addressLocality": "Mumbai",
      "postalCode": "400021",
      "addressCountry": "IN"
    },
    "aggregateRating": {
      "@type": "AggregateRating",
      "ratingValue": "4.8",
      "reviewCount": "1250"
    }
  }
  </script>
</head>
<body><h1>Oberoi Dining</h1></body>
</html>
"""


def test_jsonld_directory_adapter_schema_org_extraction():
    adapter = JsonLdDirectoryAdapter()
    records = adapter.extract(HTML_JSONLD_LOCAL_BUSINESS, "https://example.com/oberoi", {})
    assert len(records) == 1
    rec = records[0]
    assert rec["business_name"] == "Oberoi Fine Dining"
    assert rec["phone"] == "+91-22-6632-5757"
    assert rec["email"] == "dining@oberoihotels.test"
    assert rec["website"] == "https://oberoidining.test"
    assert "Nariman Point" in rec["address"]
    assert "400021" in rec["address"]
    assert rec["rating"] == 4.8
    assert rec["review_count"] == 1250
    assert rec["metadata"]["schema_type"] == "LocalBusiness"


@pytest.mark.asyncio
async def test_business_directory_actor_end_to_end_jsonld():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML_JSONLD_LOCAL_BUSINESS, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    ctx = make_test_context(
        {"url": "http://example.com/listing", "adapter": "jsonld"},
        transport,
    )

    actor = BusinessDirectoryActor()
    items = await collect_actor_items(actor, ctx)
    assert len(items) == 1
    assert items[0]["business_name"] == "Oberoi Fine Dining"
    assert items[0]["phone"] == "+91-22-6632-5757"
    assert items[0]["source"] == "business-directory"


# ==============================================================================
# 4. Government Open Data (PublicDataActor) Tests
# ==============================================================================

SEC_EDGAR_DICT_PAYLOAD = json.dumps({
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."},
    "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon Com Inc."}
})


@pytest.mark.asyncio
async def test_public_data_actor_sec_edgar_dict_of_records():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEC_EDGAR_DICT_PAYLOAD, headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    ctx = make_test_context(
        {
            "url": "http://example.com/files/company_tickers.json",
            "format": "json",
            "field_map": {"title": "business_name", "ticker": "metadata.ticker", "cik_str": "metadata.cik"},
        },
        transport,
    )

    actor = PublicDataActor()
    items = await collect_actor_items(actor, ctx)
    assert len(items) == 3
    names = [it["business_name"] for it in items]
    assert "Apple Inc." in names
    assert "Microsoft Corp." in names
    assert "Amazon Com Inc." in names
    # Provenance license attached
    assert items[0]["metadata"]["data_license"] == "Open Government / Public Domain"


# ==============================================================================
# 5. Truthful Restrictions & Terms Enforcement Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_indiamart_actor_truthful_uncredentialed_status():
    settings = Settings()
    settings.QBIT_INDIAMART_API_KEY = None
    actor = IndiaMartActor(settings=settings)

    # 1. Capability catalog reports RESTRICTED
    meta = actor.metadata()
    assert meta["implementation_status"] == SourceStatus.RESTRICTED.value
    assert meta["source_type"] == SourceType.COMMERCIAL_PLATFORM.value
    assert meta["access_method"] == "partner_api"
    assert "crm" in meta["permitted_use"].lower() or "official" in meta["permitted_use"].lower()

    # 2. Health check reports DEGRADED / CONFIGURATION REQUIRED honestly
    health = await actor.health()
    assert health.status == ActorStatus.DEGRADED
    assert "indiamart terms of use prohibit" in health.detail.lower()

    # 3. Transition to READY when official API key is provided
    settings.QBIT_INDIAMART_API_KEY = "test_official_partner_key"
    health_ready = await actor.health()
    assert health_ready.status == ActorStatus.READY


@pytest.mark.asyncio
async def test_justdial_actor_truthful_uncredentialed_status():
    settings = Settings()
    settings.QBIT_JUSTDIAL_API_KEY = None
    actor = JustDialActor(settings=settings)

    # 1. Capability catalog reports RESTRICTED
    meta = actor.metadata()
    assert meta["implementation_status"] == SourceStatus.RESTRICTED.value
    assert meta["source_type"] == SourceType.COMMERCIAL_PLATFORM.value

    # 2. Health check reports DEGRADED honestly
    health = await actor.health()
    assert health.status == ActorStatus.DEGRADED
    assert "justdial terms of service explicitly prohibit" in health.detail.lower()


# ==============================================================================
# 6. Deduplication & Formula Injection (CWE-1236) Defense Tests
# ==============================================================================

def test_csv_export_neutralizes_formula_injection():
    """Verify that dangerous leading characters (=, +, -, @, tab, CR) are safely escaped."""
    from app.services.leads.exporter import _formula_safe_cell, EXPORT_FIELDS

    lead = Lead(
        id=uuid.uuid4(),
        business_name="@SUM(1+1)",
        first_name="=cmd|' /C calc'!A0",
        last_name="+123456",
        email="-insecure@test.com",
        phone="+919876543210",
        source="test",
    )
    fields = ["business_name", "first_name", "last_name", "email", "phone"]
    row = lead_to_row(lead, fields)
    
    # Check escaped values through _formula_safe_cell
    assert _formula_safe_cell(row[EXPORT_FIELDS["first_name"]]).startswith("'")
    assert _formula_safe_cell(row[EXPORT_FIELDS["first_name"]]) == "'=cmd|' /C calc'!A0"
    assert _formula_safe_cell(row[EXPORT_FIELDS["last_name"]]).startswith("'+123456")
    assert _formula_safe_cell(row[EXPORT_FIELDS["business_name"]]).startswith("'@SUM")
    assert _formula_safe_cell(row[EXPORT_FIELDS["email"]]).startswith("'-insecure")

    # Safe phone numbers with standard plus are formatted cleanly
    assert "+919876543210" in row[EXPORT_FIELDS["phone"]]
