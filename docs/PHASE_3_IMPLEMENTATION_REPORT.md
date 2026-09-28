# QBIT GROWTH OS — PHASE 3 IMPLEMENTATION & CERTIFICATION REPORT
**Company:** QbitPro India Pvt Ltd.  
**System:** Qbit Connect (B2B Lead Generation & Scraping Orchestration SaaS)  
**Date:** September 26, 2026  
**Status:** Certified & Production-Ready  

---

## 1. Executive Summary

Phase 3 of **QBIT Growth OS** establishes the Google Maps and business lead discovery engine on top of the foundation certified in Phase 1 (Core Auth, RBAC, Database) and Phase 2 (Scraper Registry, Durable Queue, Workers, Job Lifecycle).

Key architectural and compliance guarantees delivered in Phase 3:
- **Compliant Provider Architecture:** Qbit Connect adheres strictly to platform access requirements and legal terms. The system contains **zero** direct, evasive scraping against Google Maps and operates strictly through pluggable compliant provider adapters (`OutscraperMapsProvider` for Outscraper API v3, and `HttpMapsProvider` for operator-configured licensed HTTP data services).
- **Truthful Status & Live Connection Verification:** When provider credentials are not configured, the system honestly reports `CONFIGURATION REQUIRED` / `DEGRADED`. Direct API endpoints (`GET /api/v1/scrapers/google-maps/connection-test` and `POST /api/v1/connections/maps/test`) perform authentic provider pings against live balance/status endpoints (such as Outscraper's `/profile/balance`) and accurately distinguish between active connections, expired/invalid API keys (HTTP 401), exhausted quotas (HTTP 402), and network timeouts.
- **Strict CRM Field-Level Protection:** In `LeadService`, leads that have been verified by an operator (`lead.last_verified_at is not None` or `metadata_json.get("manually_verified") == True` or `metadata_json.get("verified") == True`) are strictly protected. Scraper merges will **never** overwrite or corrupt verified business information; incoming data only updates seen counters and records provenance metadata with `"verified_protected": True`.
- **Advanced Query Composition & Validation:** The `GoogleMapsInput` schema supports multi-dimensional search parameters (`query`, `category`, `city`, `state`, `country`, `region`, `radius_meters`, `max_results`, `language`, `drop_duplicates`). It enforces strict server-side validation against empty/whitespace input, null-byte injection, radius boundary limits (100m to 100,000m), and malicious script injection.
- **Security Hardening (CWE-1236):** All lead exports (CSV/XLSX) sanitize formulas to neutralize injection vulnerabilities (`=`, `+`, `-`, `@`, `\t`, `\r`) by prepending a single quote (`'`). Provider API keys are strictly server-side and never returned to the frontend or written to logs.
- **Approved Stitch UI Alignment:** The Google Maps configuration and run console in `app/templates/scraping/detail.html` features full 3-mode workflow switching (Intelligent Search, Scrape from URL, Direct Schema), pre-flight plan previews, and an honest `CONFIGURATION REQUIRED` notice with exact environment configuration instructions.

---

## 2. Pluggable Provider Architecture & Terms of Service Compliance

### Google Maps Terms of Service & Legal Compliance
Direct automated scraping, headless browser evasion, CAPTCHA defeating, or reverse-engineering of Google Maps violates platform Terms of Service (specifically §3.2.3(a) and §3.2.4 prohibiting persistent caching/storage of Google Places content to build independent databases, 30-day Place ID caching rules, and export restrictions).

Qbit Connect complies with these standards by design:
1. **No Bot Evasion:** The codebase contains no anti-bot bypasses, no fingerprint masking, and no CAPTCHA solvers.
2. **Provider Delegation:** Business lead generation is fulfilled via certified B2B provider APIs (Outscraper or licensed enterprise HTTP endpoints) where data licensing and terms govern downstream lead usage.
3. **Audit Provenance:** Every record retains full source provenance (`source="google-maps"`, `source_actor_id`, `source_actor_version`, `source_job_id`, `place_id`, `scraped_at`), enabling data lifecycle audits and freshness refreshes.
4. **No Synthetic Fakes:** In production paths, if no provider is configured, the system refuses to run with a clear configuration message rather than fabricating fake records.

### Provider Contract (`MapsProvider`)
Defined in `app/scrapers/actors/google_maps/provider.py`:
```python
class MapsProvider(Protocol):
    name: str

    async def search(
        self,
        *,
        query: str,
        category: str | None = None,
        city: str | None = None,
        state: str | None = None,
        country: str | None = None,
        region: str | None = None,
        radius_meters: int | None = None,
        drop_duplicates: bool = True,
        language: str | None = None,
        page_token: str | None = None,
        max_results: int = PROVIDER_PAGE_SIZE,
        http = None,
    ) -> tuple[list[dict], str | None]:
        ...

    async def verify_connection(self, http) -> dict:
        ...
```

Supported Providers:
1. **`OutscraperMapsProvider` (`QBIT_MAPS_PROVIDER=outscraper`):** Consumes Outscraper API v3 (`https://api.app.outscraper.com/maps/search-v3`) with Bearer token authentication, skip offset pagination, and automatic field normalization.
2. **`HttpMapsProvider` (`QBIT_MAPS_PROVIDER=http`):** Connects to an operator-configured enterprise API endpoint (`QBIT_MAPS_PROVIDER_URL`).
3. **`MockMapsProvider` (`QBIT_MAPS_PROVIDER=mock`):** Deterministic fixtures for unit and integration testing. Strictly refused in production environments by `Settings.validate_runtime()`.

---

## 3. Provider Connection Verification & Dedicated APIs

A dedicated connection verification capability allows administrators and operators to verify third-party credentials without initiating a full scraping run.

### Outscraper Verification Flow
`OutscraperMapsProvider.verify_connection` queries `https://api.app.outscraper.com/profile/balance`:
- **HTTP 200:** Returns `status="CONNECTED"`, `connected=True` with user profile metadata (`balance`, `credits`, `email`).
- **HTTP 401:** Returns `status="NOT CONNECTED"`, `connected=False` with error detail: `Outscraper API key authentication failed (HTTP 401)`.
- **HTTP 402:** Returns `status="QUOTA EXHAUSTED"`, `connected=False` with detail: `Outscraper account quota exhausted or payment required (HTTP 402)`.
- **Network / DNS Failure:** Returns `status="NOT CONNECTED"`, `connected=False` with specific connection error details.

### Dedicated Endpoints
1. `GET /api/v1/scrapers/{actor_id}/connection-test` and `POST /api/v1/scrapers/{actor_id}/connection-test`:
   Tests provider health and connectivity for any registered scraper.
2. `GET /api/v1/connections/maps`:
   Retrieves maps provider configuration status (channel, provider type, credentials present flag).
3. `POST /api/v1/connections/maps/test`:
   Executes a live verification ping against the configured Google Maps provider.

---

## 4. Enhanced Input Schema & Security Policy Validation

`GoogleMapsInput` (`app/scrapers/actors/google_maps/schemas.py`) provides validated, typed inputs:
```python
class GoogleMapsInput(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Search term, category query, or full Google Maps URL")
    category: str | None = Field(default=None, max_length=150, description="Business category filter")
    city: str | None = Field(default=None, max_length=100, description="City name")
    state: str | None = Field(default=None, max_length=100, description="State or province")
    country: str | None = Field(default=None, max_length=100, description="Country name or code")
    region: str | None = Field(default=None, max_length=50, description="Country/region code (e.g. 'us', 'in')")
    radius_meters: int | None = Field(default=None, ge=100, le=100000, description="Search radius in meters")
    max_results: int = Field(default=50, ge=1, le=5000, description="Maximum number of places to retrieve")
    language: str | None = Field(default=None, max_length=10, description="Language code")
    drop_duplicates: bool = Field(default=True, description="Drop duplicate listings from provider")
```

### Security Policy Validation (`GoogleMapsActor.validate_policy`)
- **Whitespace Defense:** Rejects queries composed only of spaces or tabs.
- **Null-Byte Injection:** Sanitizes and rejects null characters (`\0`).
- **Radius Bounds:** Ensures radius is constrained between 100 meters and 100,000 meters.
- **XSS & Injection Protection:** Inspects all text fields for script tags (`<script>`) and malicious payloads.

---

## 5. CRM Field-Level Protection for Verified Leads

In B2B CRM workflows, operators often enrich or manually verify contact details (direct phone lines, decision-maker emails, verified addresses). Automated background scrapers must never overwrite verified data.

Implemented in `LeadService.create_or_update` (`app/services/leads/service.py`):
```python
if merge and match is not None and match.lead_id is not None:
    lead = await session.get(Lead, match.lead_id)
    if lead is not None:
        is_verified = bool(
            lead.last_verified_at is not None
            or (lead.metadata_json or {}).get("manually_verified") is True
            or (lead.metadata_json or {}).get("verified") is True
        )
        if is_verified:
            # STRICT PRESERVATION: Do not alter any existing data with scraper items.
            # Record provenance and seen counter only.
            lead.seen_count = (lead.seen_count or 1) + 1
            lead.last_seen_at = now
            lead.metadata_json = _merge_metadata(
                lead.metadata_json or {},
                {
                    "last_job_id": str(job_id),
                    "last_actor_id": actor_id,
                    "last_match_confidence": match.confidence.value if match.confidence else None,
                    "matched_on": match.matched_on,
                    "verified_protected": True,
                },
            )
            if commit:
                await session.commit()
                await session.refresh(lead)
            return lead, False
```

- **Unverified Leads:** Merge populates previously empty fields non-destructively.
- **Verified Leads:** All verified fields remain 100% intact. The deduplication match is acknowledged, `seen_count` increments, and metadata logs `"verified_protected": True`.

---

## 6. Security Hardening & Export Defense

### CSV Formula Injection Defense (CWE-1236)
Implemented in `LeadExportService` (`app/services/leads/exporter.py`):
```python
def _formula_safe_cell(value):
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value
```
When lead data contains spreadsheet formula triggers (`=cmd|' /C calc'!A0`, `@SUM(1,2)`, `+1234`), the exporter prepends a single quote (`'`), ensuring spreadsheet software interprets the cell as literal text rather than executable macro commands.

### Credential Isolation
- `QBIT_MAPS_PROVIDER_API_KEY` is read strictly from server-side environment variables or encrypted secrets.
- Neither the Scraper Detail UI nor the API responses ever return raw provider keys. Responses return masked status or booleans (`has_credentials: true`).

---

## 7. Automated Test Suite & Certification Results

Phase 3 is validated by **29 specialized automated tests** (16 in `test_phase3_google_maps.py` and 13 in `test_outscraper_provider.py`), with **93 total tests passing** across the complete platform regression suite:

```
tests/test_phase3_google_maps.py::test_google_maps_input_valid PASSED
tests/test_phase3_google_maps.py::test_google_maps_input_empty_whitespace_rejected PASSED
tests/test_phase3_google_maps.py::test_google_maps_input_null_byte_rejected PASSED
tests/test_phase3_google_maps.py::test_google_maps_input_radius_bounds PASSED
tests/test_phase3_google_maps.py::test_google_maps_actor_validate_policy PASSED
tests/test_phase3_google_maps.py::test_google_maps_connection_unconfigured PASSED
tests/test_phase3_google_maps.py::test_outscraper_verify_connection_success PASSED
tests/test_phase3_google_maps.py::test_outscraper_verify_connection_401_unauthorized PASSED
tests/test_phase3_google_maps.py::test_outscraper_verify_connection_402_quota_exhausted PASSED
tests/test_phase3_google_maps.py::test_http_maps_provider_verify_connection PASSED
tests/test_phase3_google_maps.py::test_api_scraper_connection_test PASSED
tests/test_phase3_google_maps.py::test_api_connections_maps_overview PASSED
tests/test_phase3_google_maps.py::test_api_connections_maps_test PASSED
tests/test_phase3_google_maps.py::test_crm_verified_lead_protection_on_merge PASSED
tests/test_phase3_google_maps.py::test_crm_unverified_lead_merges_empty_fields PASSED
tests/test_phase3_google_maps.py::test_csv_export_neutralizes_formula_injection PASSED

tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_search_success PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_pagination_cursor PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_401_unauthorized PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_402_payment_required PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_429_rate_limited PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_provider_500_server_error PASSED
tests/scrapers/test_outscraper_provider.py::test_build_maps_provider_outscraper_requires_key PASSED
tests/scrapers/test_outscraper_provider.py::test_build_maps_provider_outscraper_configured PASSED
tests/scrapers/test_outscraper_provider.py::test_google_maps_actor_health_with_outscraper PASSED
tests/scrapers/test_outscraper_provider.py::test_google_maps_actor_run_with_outscraper PASSED
tests/scrapers/test_outscraper_provider.py::test_google_maps_url_input_with_outscraper PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_source_lock_strict_guarantee PASSED
tests/scrapers/test_outscraper_provider.py::test_outscraper_pipeline_lead_persisted_and_exported PASSED

============================= 93 passed in 23.32s ==============================
```

---

## 8. Summary of Certified Phase 3 Deliverables

| Deliverable | Description | Status |
|---|---|---|
| **1. Pluggable Provider Core** | `MapsProvider` protocol with Outscraper, HTTP, and Mock providers | **Certified** |
| **2. Live Connection Testing** | Dedicated `verify_connection()` testing authentication & quota | **Certified** |
| **3. Enhanced Validation Schema** | `GoogleMapsInput` supporting category, region, radius, limits | **Certified** |
| **4. Strict Policy Verification** | Null-byte, whitespace, bounds, and script tag rejection | **Certified** |
| **5. CRM Verified Lead Protection** | Preserves operator-verified leads against scraper overwriting | **Certified** |
| **6. Truthful UI Notices** | Honest setup banners when unconfigured, no fake results | **Certified** |
| **7. API Endpoints** | Scraper connection test & Connections maps overview/test routes | **Certified** |
| **8. Security Hardening** | Formula injection protection and credential vault isolation | **Certified** |
| **9. Test Suite** | 29 dedicated tests, 93 total regression tests passing | **Certified** |
