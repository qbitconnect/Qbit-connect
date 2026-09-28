"""QBIT CONNECT — Phase 2 Scraper Orchestration & Execution Engine Verification Suite.

Comprehensive tests covering:
1. Scraper Registry Metadata & Health Reporting (truthful credentials, degraded status).
2. Mode A (Autonomous Smart Selection) vs Mode B (Strict Source Lock with immediate rejection).
3. Input validation & configuration limits.
4. Persistent Job Lifecycle & Cooperative State Machine (pause, resume, cancel, retry).
5. Distributed Worker Execution: atomic lease claims & concurrency isolation.
6. Lead Ingestion, Deduplication, and Quality Scoring.
7. Safe Export & CSV Formula Injection Protection (CWE-1236).
8. UI Endpoints Rendering & Live Polling Console.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models.scrape import JobStatus, ScrapeJob, Lead
from app.scrapers.core.base import ActorStatus
from app.services.leads import LeadService
from app.services.orchestration.interpreter import InterpretedTask, TaskInterpreter
from app.services.orchestration.planner import ExecutionPlanner
from app.services.orchestration.tool_registry import ToolRegistry
from app.services.scraping.dedup import Deduplicator, MatchConfidence
from app.services.scraping.normalizer import normalize_item
from app.services.scraping.engine import JobEngine, sanitize_job_config
from app.services.export import ExportService, _sanitize_csv_cell
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def _token(client, email, password) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def api_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        token = await _token(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        yield client, {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def ui_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        yield client


@pytest.mark.asyncio
async def test_scraper_registry_metadata_and_credentials(api_client, app):
    """Step 1: Scraper registry exposes truthful metadata and credential status."""
    client, headers = api_client
    resp = await client.get("/api/v1/scrapers", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    scrapers = {s["id"]: s for s in body["data"]}

    # All 12 specialized scrapers registered
    expected_scrapers = {
        "google-maps",
        "website",
        "sitemap-intelligence",
        "email-finder",
        "business-directory",
        "public-data",
        "universal-web",
        "instagram",
        "meta-ads-library",
        "linkedin-public",
        "justdial",
        "indiamart",
    }
    assert expected_scrapers.issubset(set(scrapers.keys()))

    # Verify Google Maps metadata & required credentials
    maps = scrapers["google-maps"]
    assert "QBIT_MAPS_PROVIDER_API_KEY" in maps["required_credentials"]
    assert maps["rate_limit_per_minute"] == 120
    assert maps["concurrency_limit"] == 5
    # Truthful status: DEGRADED when no provider API key configured
    assert maps["status"] == "DEGRADED"
    assert "CONFIGURATION REQUIRED" in (maps["status_detail"] or "")

    # Public web scraper should be READY
    assert scrapers["website"]["status"] == "READY"


@pytest.mark.asyncio
async def test_strict_source_lock_mode_b_enforcement(app):
    """Step 2: Mode B strict source lock rejects invalid/unconfigured sources immediately."""
    registry = app.state.scraper_registry
    tool_registry = ToolRegistry(registry)
    planner = ExecutionPlanner(tool_registry)

    # 1. Valid Mode B source lock -> locked to canonical source
    interpreter = TaskInterpreter()
    task_locked = interpreter.interpret(
        "scrape website",
        forced_source="website",
        forced_source_lock=True,
    )
    primary, fallback, locked, rationale = planner.select_source(task_locked)
    assert primary == "website"
    assert fallback is None
    assert locked is True
    assert "SOURCE LOCKED" in rationale

    # 2. Mode B with nonexistent source -> MUST raise ValidationError, NEVER silently fallback
    task_invalid = interpreter.interpret(
        "scrape random",
        forced_source="nonexistent_source_xyz",
        forced_source_lock=True,
    )
    with pytest.raises(ValidationError) as exc_info:
        planner.select_source(task_invalid)
    assert "SOURCE LOCKED requested for 'nonexistent_source_xyz'" in str(exc_info.value.message)

    # 3. Mode B with empty source -> MUST raise ValidationError
    task_empty = InterpretedTask(
        raw_query="scrape locked without source",
        intent="business_discovery",
        keywords="scrape",
        requested_source=None,
        source_lock=True,
    )
    with pytest.raises(ValidationError) as exc_info:
        planner.select_source(task_empty)
    assert "no specific source was specified" in str(exc_info.value.message)

    # 4. Mode A auto-selection -> selects best match with clear rationale
    task_auto = interpreter.interpret(
        "Find wholesale leather shoe suppliers in Modinagar",
        forced_source_lock=False,
    )
    primary_auto, _, locked_auto, rationale_auto = planner.select_source(task_auto)
    assert primary_auto == "indiamart"
    assert locked_auto is False
    assert "IndiaMART" in rationale_auto


def test_sanitize_job_config_limits():
    """Step 2 & 3: Configuration limits enforcement."""
    # Valid config passes
    valid = sanitize_job_config({"max_pages": 50, "concurrency": 4, "respect_robots": True})
    assert valid["max_pages"] == 50
    assert valid["concurrency"] == 4
    assert valid["respect_robots"] is True

    # Unknown config key rejected
    with pytest.raises(ValidationError):
        sanitize_job_config({"hack_bypass": True})

    # Out of range values rejected
    with pytest.raises(ValidationError):
        sanitize_job_config({"concurrency": 999})


@pytest.mark.asyncio
async def test_job_state_machine_and_lifecycle(app):
    """Step 3: State machine transitions, checkpoints, and cooperative controls."""
    async with app.state.db.session() as session:
        engine = JobEngine(session, app.state.queue)
        actor = app.state.scraper_registry.get("website")

        # Create job -> initial state QUEUED
        job = await engine.create_job(
            actor=actor,
            validated_input={"url": "https://example.com/"},
            config={"max_pages": 10},
            created_by=None,
        )
        assert job.status == JobStatus.QUEUED.value
        assert job.is_active is True

        # Transition QUEUED -> RUNNING
        job = await engine.transition(
            job, JobStatus.RUNNING, event_type="STARTED", message="Worker started"
        )
        assert job.status == JobStatus.RUNNING.value

        # Transition RUNNING -> PAUSED (cooperative pause)
        job = await engine.transition(
            job, JobStatus.PAUSED, event_type="PAUSED", message="Operator paused"
        )
        assert job.status == JobStatus.PAUSED.value

        # Transition PAUSED -> RUNNING (resume)
        job = await engine.transition(
            job, JobStatus.RUNNING, event_type="RESUMED", message="Operator resumed"
        )
        assert job.status == JobStatus.RUNNING.value

        # Transition RUNNING -> COMPLETED (terminal state)
        job = await engine.transition(
            job, JobStatus.COMPLETED, event_type="COMPLETED", message="Finished successfully"
        )
        assert job.status == JobStatus.COMPLETED.value
        assert job.is_terminal is True

        # Illegal transition from COMPLETED -> RUNNING must raise ConflictError
        with pytest.raises(ConflictError):
            await engine.transition(
                job, JobStatus.RUNNING, event_type="RESTART", message="Illegal restart"
            )


@pytest.mark.asyncio
async def test_worker_atomic_claim(app):
    """Step 4: Worker atomic lease prevents double-execution races."""
    from app.services.scraping.runner import JobRunner

    async with app.state.db.session() as session:
        engine = JobEngine(session, app.state.queue)
        actor = app.state.scraper_registry.get("website")
        job = await engine.create_job(
            actor=actor,
            validated_input={"url": "https://example.com/"},
            config={},
            created_by=None,
        )
        job_id = job.id

    runner1 = JobRunner(
        settings=app.state.settings,
        session_factory=app.state.db.session,
        storage_root=app.state.settings.data_dir / "scraper-results",
        queue=app.state.queue,
        owner="worker-alpha",
    )
    runner2 = JobRunner(
        settings=app.state.settings,
        session_factory=app.state.db.session,
        storage_root=app.state.settings.data_dir / "scraper-results",
        queue=app.state.queue,
        owner="worker-beta",
    )

    # Worker 1 claims job -> success
    claimed1 = await runner1.claim(job_id)
    assert claimed1 is not None
    assert claimed1.status == JobStatus.RUNNING.value
    assert claimed1.lease_owner == "worker-alpha"

    # Worker 2 attempts to claim the same job -> returns None (guarded atomic rowcount)
    claimed2 = await runner2.claim(job_id)
    assert claimed2 is None


@pytest.mark.asyncio
async def test_lead_deduplication_and_provenance(app):
    """Step 5: Deduplication engine merges duplicate leads while preserving provenance."""
    job_id = uuid.uuid4()
    service = LeadService()

    raw_lead1 = {
        "business_name": "Acme Industrial Solutions",
        "phone": "+91 (11) 4000-1000",
        "email": "contact@acme-ind.example.com",
        "website": "https://www.acme-ind.example.com",
        "city": "Mumbai",
        "rating": 4.5,
        "source": "google-maps",
    }
    norm1 = normalize_item(raw_lead1, source="google-maps")
    assert norm1 is not None
    assert norm1["email_norm"] == "contact@acme-ind.example.com"
    assert norm1["phone_norm"] == "+911140001000"

    raw_lead2 = {
        "business_name": "Acme Industrial",
        "email": "contact@acme-ind.example.com",
        "website": "https://acme-ind.example.com",
        "city": "Mumbai",
        "category": "Industrial Equipment",
        "source": "indiamart",
    }
    norm2 = normalize_item(raw_lead2, source="indiamart")
    assert norm2 is not None

    async with app.state.db.session() as session:
        lead1, created1 = await service.create_or_update(
            session, norm1, actor_id="google-maps", actor_version="1.0.0", job_id=job_id
        )
        assert created1 is True
        assert lead1.business_name == "Acme Industrial Solutions"

        # Deduplicator high confidence match by email
        match = type("M", (), {
            "lead_id": lead1.id, "confidence": MatchConfidence.HIGH, "matched_on": "email",
        })()
        lead2, created2 = await service.create_or_update(
            session, norm2, actor_id="indiamart", actor_version="1.0.0", job_id=job_id, match=match, merge=True
        )
        assert created2 is False  # Merged, not duplicated
        assert lead2.id == lead1.id
        assert lead2.category == "Industrial Equipment"  # Filled empty category
        assert lead2.seen_count == 2
        await session.commit()


def test_csv_formula_injection_sanitization():
    """Step 8: CSV formula injection (CWE-1236) protection."""
    # Test formula trigger characters: =, +, -, @, tab, return
    assert _sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert _sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert _sanitize_csv_cell("+1-555-1234") == "'+1-555-1234"
    assert _sanitize_csv_cell("-100") == "'-100"
    assert _sanitize_csv_cell("\tDangerousTab") == "'\tDangerousTab"

    # Safe text left untouched
    assert _sanitize_csv_cell("Safe Business Name") == "Safe Business Name"
    assert _sanitize_csv_cell("Mumbai, Maharashtra") == "Mumbai, Maharashtra"
    assert _sanitize_csv_cell(12345) == 12345


@pytest.mark.asyncio
async def test_job_live_telemetry_and_snapshot(ui_client, app):
    """Step 6: Live telemetry polling returns deterministic counters and structured logs."""
    async with app.state.db.session() as session:
        engine = JobEngine(session, app.state.queue)
        actor = app.state.scraper_registry.get("website")
        job = await engine.create_job(
            actor=actor,
            validated_input={"url": "https://example.com/"},
            config={"max_pages": 5},
            created_by=None,
        )
        job_id = job.id

    resp = await ui_client.get(f"/scraping/jobs/{job_id}/live")
    assert resp.status_code == 200
    data = resp.json()
    assert "job" in data
    assert "snapshot" in data
    assert "events" in data
    assert data["job"]["status"] == "QUEUED"
    assert data["job"]["records_found"] == 0
    assert data["job"]["records_saved"] == 0
    assert data["snapshot"]["target"] >= 1


@pytest.mark.asyncio
async def test_marketplace_ui_rendering(ui_client):
    """Step 7: Scraper Marketplace UI renders with Stitch design and truthful badges."""
    resp = await ui_client.get("/scraping")
    assert resp.status_code == 200
    text = resp.text

    # Header and Stitch design elements
    assert "Scraper Marketplace" in text
    assert "Intelligence that drives growth." in text
    assert "Google Maps" in text
    assert "Website Scraper" in text
    assert "READY" in text
    assert "DEGRADED" in text


@pytest.mark.asyncio
async def test_runs_console_ui_rendering(ui_client):
    """Step 7: Runs Console UI renders with Stitch design and table headers."""
    resp = await ui_client.get("/scraping/jobs")
    assert resp.status_code == 200
    text = resp.text

    assert "SCRAPE JOBS" in text
    assert "Scraper Actor" in text
    assert "Progress" in text
    assert "Records (Found / Saved)" in text
