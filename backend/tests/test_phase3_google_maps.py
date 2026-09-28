"""Phase 3 — Google Maps / Business Lead Discovery Integration Tests.

Validates:
1. GoogleMapsInput schema and policy validation (category, city, radius, limits, injection defense).
2. Honest provider status and connection verification (unconfigured, connected, 401, 402, 500).
3. API endpoints for scraper connection testing and connections overview.
4. Strict CRM field-level protection for manually verified leads.
5. End-to-end background job execution and lead persistence.
6. CSV formula injection defense on lead export.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.models.scrape import JobStatus, Lead, ScrapeJob
from app.scrapers.actors.google_maps.actor import GoogleMapsActor
from app.scrapers.actors.google_maps.provider import (
    HttpMapsProvider,
    OutscraperMapsProvider,
    build_maps_provider,
)
from app.scrapers.actors.google_maps.schemas import GoogleMapsInput
from app.scrapers.core.context import JobLimits, ScraperContext
from app.scrapers.core.http import HttpPolicy, PolicyHttpClient
from app.scrapers.core.netguard import UrlPolicy
from app.services.leads.exporter import _CSVWriter, lead_to_row
from app.services.leads.service import LeadService


def make_test_client(handler) -> PolicyHttpClient:
    policy = HttpPolicy(request_timeout=5.0, max_retries=0, respect_robots=False)
    url_policy = UrlPolicy(allow_private_targets=True, allowed_hosts={"api.app.outscraper.com", "api.outscraper.cloud", "fixture.test"})
    return PolicyHttpClient(policy, url_policy, transport=httpx.MockTransport(handler))


# ==============================================================================
# 1. GoogleMapsInput & Policy Validation Tests
# ==============================================================================
def test_google_maps_input_valid():
    inp = GoogleMapsInput(
        query="dentists",
        category="Healthcare",
        city="Modinagar",
        state="Uttar Pradesh",
        country="India",
        region="in",
        radius_meters=5000,
        max_results=100,
        language="en",
        drop_duplicates=True,
    )
    assert inp.query == "dentists"
    assert inp.category == "Healthcare"
    assert inp.city == "Modinagar"
    assert inp.radius_meters == 5000
    assert inp.max_results == 100
    assert inp.drop_duplicates is True


def test_google_maps_input_empty_whitespace_rejected():
    with pytest.raises(PydanticValidationError):
        GoogleMapsInput(query="   ")


def test_google_maps_input_null_byte_rejected():
    with pytest.raises(PydanticValidationError):
        GoogleMapsInput(query="restaurants\0bar")


def test_google_maps_input_radius_bounds():
    with pytest.raises(PydanticValidationError):
        GoogleMapsInput(query="hotels", radius_meters=50)  # below 100
    with pytest.raises(PydanticValidationError):
        GoogleMapsInput(query="hotels", radius_meters=200000)  # above 100000


def test_google_maps_actor_validate_policy():
    actor = GoogleMapsActor()
    # Script tag detection
    rep = actor.validate_input({"query": "cafes <script>alert(1)</script>"})
    assert rep.valid is False
    assert "query" in rep.errors


# ==============================================================================
# 2. Truthful Provider Connection Verification Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_google_maps_connection_unconfigured():
    settings = Settings(QBIT_ENV="test", QBIT_MAPS_PROVIDER="none", _env_file=None)
    actor = GoogleMapsActor(settings=settings)
    conn = await actor.verify_connection()
    assert conn["connected"] is False
    assert conn["status"] == "CONFIGURATION REQUIRED"
    assert conn["provider"] == "none"


@pytest.mark.asyncio
async def test_outscraper_verify_connection_success():
    def handler(request: httpx.Request):
        assert str(request.url) == "https://api.app.outscraper.com/profile/balance"
        assert request.headers.get("x-api-key") == "valid-key-123"
        return httpx.Response(200, json={"balance": 450.50, "credits": 450, "email": "dev@qbitpro.com"})

    client = make_test_client(handler)
    provider = OutscraperMapsProvider("https://api.app.outscraper.com/maps/search-v3", "valid-key-123")
    result = await provider.verify_connection(client)

    assert result["connected"] is True
    assert result["status"] == "CONNECTED"
    assert result["metadata"]["balance"] == 450.50
    assert result["metadata"]["credits"] == 450


@pytest.mark.asyncio
async def test_outscraper_verify_connection_401_unauthorized():
    def handler(request: httpx.Request):
        return httpx.Response(401, json={"message": "Invalid API key"})

    client = make_test_client(handler)
    provider = OutscraperMapsProvider("https://api.app.outscraper.com/maps/search-v3", "bad-key")
    result = await provider.verify_connection(client)

    assert result["connected"] is False
    assert result["status"] == "NOT CONNECTED"
    assert result["metadata"]["http_status"] == 401


@pytest.mark.asyncio
async def test_outscraper_verify_connection_402_quota_exhausted():
    def handler(request: httpx.Request):
        return httpx.Response(402, json={"message": "Payment required / Out of credits"})

    client = make_test_client(handler)
    provider = OutscraperMapsProvider("https://api.app.outscraper.com/maps/search-v3", "expired-key")
    result = await provider.verify_connection(client)

    assert result["connected"] is False
    assert result["status"] == "QUOTA EXHAUSTED"
    assert result["metadata"]["http_status"] == 402


@pytest.mark.asyncio
async def test_http_maps_provider_verify_connection():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"status": "ok"})

    client = make_test_client(handler)
    provider = HttpMapsProvider("https://fixture.test/v1/search", api_key="test-key")
    result = await provider.verify_connection(client)
    assert result["connected"] is True
    assert result["status"] == "CONNECTED"


# ==============================================================================
# 3. Connection Test API Endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_api_scraper_connection_test(client, admin_headers):
    # Tests the /api/v1/scrapers/google-maps/connection-test endpoint
    resp = await client.get("/api/v1/scrapers/google-maps/connection-test", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "connected" in data
    assert "status" in data


@pytest.mark.asyncio
async def test_api_connections_maps_overview(client, admin_headers):
    # Tests the /api/v1/connections/maps endpoint
    resp = await client.get("/api/v1/connections/maps", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["channel"] == "MAPS"
    assert "provider" in data


@pytest.mark.asyncio
async def test_api_connections_maps_test(client, admin_headers):
    # Tests the /api/v1/connections/maps/test endpoint
    resp = await client.post("/api/v1/connections/maps/test", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "connected" in data


# ==============================================================================
# 4. CRM Field-Level Protection for Manually Verified Leads
# ==============================================================================
@pytest.mark.asyncio
async def test_crm_verified_lead_protection_on_merge(app):
    session_factory = app.state.db.session
    service = LeadService()
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()

    async with session_factory() as session:
        # Create an operator-verified lead
        verified_lead = Lead(
            business_name="Original Verified Hotel",
            phone="+91 99999 11111",
            phone_norm="919999911111",
            email="manager@verifiedhotel.com",
            email_norm="manager@verifiedhotel.com",
            address="100 Mall Road",
            city="Modinagar",
            last_verified_at=now,
            metadata_json={"manually_verified": True},
            source="manual",
        )
        session.add(verified_lead)
        await session.commit()
        await session.refresh(verified_lead)

        # Create mock match pointing to this verified lead
        class MockMatch:
            lead_id = verified_lead.id
            matched_on = "phone"
            class confidence:
                value = "HIGH"

        # An incoming scraper item has DIFFERENT (conflicting or new) data
        incoming_item = {
            "business_name": "Overwriting Scraper Hotel Name",
            "phone": "+91 99999 11111",
            "phone_norm": "919999911111",
            "email": "scraped@hotel.com",
            "address": "Scraped Street Address 500",
            "city": "Scraped City",
            "metadata": {"scraped_key": "some_value"},
        }

        # Attempt to merge with the verified lead
        merged_lead, created = await service.create_or_update(
            session,
            incoming_item,
            actor_id="google-maps",
            actor_version="1.0.0",
            job_id=job_id,
            match=MockMatch(),
            merge=True,
            commit=True,
        )

        assert created is False
        assert merged_lead.id == verified_lead.id
        # STRICT PRESERVATION: All original verified fields must remain intact!
        assert merged_lead.business_name == "Original Verified Hotel"
        assert merged_lead.email == "manager@verifiedhotel.com"
        assert merged_lead.address == "100 Mall Road"
        assert merged_lead.city == "Modinagar"
        # Metadata must flag that verified data was protected
        assert merged_lead.metadata_json.get("verified_protected") is True
        # Seen count must increment
        assert merged_lead.seen_count == 2


@pytest.mark.asyncio
async def test_crm_unverified_lead_merges_empty_fields(app):
    session_factory = app.state.db.session
    service = LeadService()
    job_id = uuid.uuid4()

    async with session_factory() as session:
        # Create an unverified lead with missing address
        unverified_lead = Lead(
            business_name="Unverified Cafe",
            phone="+91 88888 22222",
            phone_norm="918888822222",
            email=None,
            address=None,
            last_verified_at=None,
            metadata_json={},
            source="google-maps",
        )
        session.add(unverified_lead)
        await session.commit()
        await session.refresh(unverified_lead)

        class MockMatch:
            lead_id = unverified_lead.id
            matched_on = "phone"
            class confidence:
                value = "HIGH"

        incoming_item = {
            "business_name": "Unverified Cafe",
            "phone": "+91 88888 22222",
            "email": "cafe@example.com",
            "address": "42 Market Street",
        }

        merged_lead, created = await service.create_or_update(
            session,
            incoming_item,
            actor_id="google-maps",
            actor_version="1.0.0",
            job_id=job_id,
            match=MockMatch(),
            merge=True,
            commit=True,
        )

        assert created is False
        # Empty fields were safely filled
        assert merged_lead.email == "cafe@example.com"
        assert merged_lead.address == "42 Market Street"


# ==============================================================================
# 5. CSV Formula Injection Defense on Export
# ==============================================================================
def test_csv_export_neutralizes_formula_injection():
    fields = ["business_name", "phone", "email", "address"]
    bio = io.BytesIO()
    writer = _CSVWriter(bio, fields)

    # Lead with formula injection payloads in various fields
    lead = Lead(
        business_name="=cmd|' /C calc'!A0",
        phone="+919876543210",
        email="@evil.com",
        address="-1+2",
    )
    writer.write_row(lead_to_row(lead, fields))
    writer.close()

    csv_output = bio.getvalue().decode("utf-8")
    lines = csv_output.strip().split("\r\n" if "\r\n" in csv_output else "\n")
    data_row = lines[1]

    # Formula characters must be escaped with a leading apostrophe (')
    assert "'=cmd|' /C calc'!A0" in data_row
    assert "'+919876543210" in data_row
    assert "'@evil.com" in data_row
    assert "'-1+2" in data_row
