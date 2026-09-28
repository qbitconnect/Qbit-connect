"""Operator UI tests (brief §33–§37): login flow, cards, forms, job pages, RBAC."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, VIEWER_EMAIL, VIEWER_PASSWORD


@pytest_asyncio.fixture
async def ui_client(app):
    transport = ASGITransport(app=app)  # redirects NOT followed: assertions on 303s
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _login(ui_client: AsyncClient, email: str, password: str):
    resp = await ui_client.post(
        "/login",
        data={"email": email, "password": password, "next": "/scraping"},
    )
    return resp


async def test_anonymous_is_redirected_to_login(ui_client):
    resp = await ui_client.get("/scraping")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")

    resp_home = await ui_client.get("/")
    assert resp_home.status_code == 303
    assert resp_home.headers["location"].startswith("/login")


async def test_home_page_authenticated_renders_ceo_command_center(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/")
    assert resp.status_code == 200
    assert "Workspace OS // v2.4" in resp.text
    assert "NODE: ASIA-SOUTH1-B4" in resp.text
    assert "Leads Collected" in resp.text
    assert "Recent Scraper Runs" in resp.text


async def test_login_success_sets_cookie_and_redirects(ui_client):
    resp = await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/scraping"
    cookie = resp.headers.get("set-cookie", "")
    assert "qbit_session=" in cookie and "HttpOnly" in cookie


async def test_login_failure_shows_error(ui_client):
    resp = await _login(ui_client, ADMIN_EMAIL, "wrong-password")
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text


async def test_scraper_cards_page(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/scraping")
    assert resp.status_code == 200
    for name in ("Google Maps", "Website Scraper", "Email Finder"):
        assert name in resp.text
    assert "READY" in resp.text or "DEGRADED" in resp.text


async def test_scraper_search_filter(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/scraping", params={"q": "maps"})
    assert resp.status_code == 200
    assert "Google Maps" in resp.text
    assert "Public Data" not in resp.text


async def test_scraper_detail_page_renders_schema_form(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/scraping/website")
    assert resp.status_code == 200
    assert "Run configuration" in resp.text
    assert 'name="input__url"' in resp.text
    assert "Advanced settings" in resp.text  # collapsible (§34)


async def test_scraper_validate_endpoint(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.post(
        "/scraping/website/validate",
        json={"input": {"url": "not-a-url"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "url" in body["errors"]


async def test_run_form_creates_job_and_redirects(ui_client, app):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.post(
        "/scraping/website/run",
        data={"input__url": "https://example.com", "input__max_pages": "5"},
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/scraping/jobs/")

    # the job exists and is QUEUED (queued for the worker, §11)
    job_id = location.rsplit("/", 1)[-1]
    from sqlalchemy import select

    from app.models.scrape import ScrapeJob

    db = app.state.db
    async with db.session() as session:
        job = await session.get(ScrapeJob, uuid.UUID(job_id))
        assert job is not None
        assert job.status == "QUEUED"
        assert job.actor_id == "website"
        assert job.input["url"] == "https://example.com/"


async def test_run_form_rejects_invalid_input(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.post(
        "/scraping/website/run", data={"input__url": "not-a-url"}
    )
    assert resp.status_code == 422
    assert "url" in resp.text


async def test_job_list_and_detail_pages(ui_client, app):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.post(
        "/scraping/website/run",
        data={"input__url": "https://example.com"},
    )
    job_url = resp.headers["location"]

    listing = await ui_client.get("/scraping/jobs")
    assert listing.status_code == 200
    assert "SCRAPE JOBS" in listing.text

    detail = await ui_client.get(job_url)
    assert detail.status_code == 200
    assert "SCRAPE JOB" in detail.text
    assert "QUEUED" in detail.text
    assert "Pause" in detail.text and "Cancel" in detail.text


async def test_job_pause_via_ui(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.post(
        "/scraping/website/run", data={"input__url": "https://example.com"}
    )
    job_url = resp.headers["location"]
    paused = await ui_client.post(f"{job_url}/pause")
    assert paused.status_code == 303
    detail = await ui_client.get(job_url)
    assert "PAUSED" in detail.text
    assert "Resume" in detail.text


async def test_viewer_can_view_but_not_run(app, ui_client):
    # VIEWER role carries scraping.view (read-only) but NOT scraping.run
    await _login(ui_client, VIEWER_EMAIL, VIEWER_PASSWORD)
    ok = await ui_client.get("/scraping")
    assert ok.status_code == 200
    resp = await ui_client.post(
        "/scraping/website/run", data={"input__url": "https://example.com"},
    )
    assert resp.status_code == 303  # UiRedirect → /403
    assert resp.headers["location"] == "/403"


async def test_ui_agent_plan_and_run(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)

    # 1. Preview Agent Plan
    plan_resp = await ui_client.post(
        "/scraping/agent/plan",
        json={"prompt": "Find 50 textile suppliers in Surat on indiamart", "source": "indiamart", "target_count": 50},
    )
    assert plan_resp.status_code == 200
    plan_data = plan_resp.json()
    assert plan_data["primary_tool"] == "indiamart"
    assert plan_data["source_locked"] is True
    assert plan_data["fallback_tool"] is None
    assert plan_data["target_count"] == 50

    # 2. Run Agent Plan via UI
    run_resp = await ui_client.post(
        "/scraping/agent/run",
        json={"prompt": "Find 50 textile suppliers in Surat on indiamart", "source": "indiamart", "target_count": 50},
    )
    assert run_resp.status_code == 200, f"Got {run_resp.status_code}, location={run_resp.headers.get('location')}"
    run_data = run_resp.json()
    assert "job_id" in run_data
    assert "redirect_url" in run_data

    # 3. View Job Detail with Deterministic Completion Card
    detail_resp = await ui_client.get(run_data["redirect_url"])
    assert detail_resp.status_code == 200
    assert "Deterministic Completion Engine" in detail_resp.text
    assert "UNIQUE VALID" in detail_resp.text
    assert "SYNTAX INVALID" in detail_resp.text

    # 4. Check Live Polling JSON
    live_resp = await ui_client.get(f"{run_data['redirect_url']}/live")
    assert live_resp.status_code == 200
    live_data = live_resp.json()
    assert "snapshot" in live_data
    assert live_data["snapshot"]["target"] == 50
    assert "status" in live_data["snapshot"]


async def test_connections_page_renders_stitch_screen_32(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/connections")
    assert resp.status_code == 200
    assert "Integrations &amp; Connected Accounts" in resp.text or "Integrations & Connected Accounts" in resp.text
    assert "QBIT SYSTEM // 32" in resp.text
    assert "Google Maps API" in resp.text
    assert "API Credentials &amp; Environment Secrets" in resp.text or "API Credentials & Environment Secrets" in resp.text


async def test_admin_teams_page_renders_stitch_screen_34(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/admin/teams")
    assert resp.status_code == 200
    assert "Team Management &amp; Groups" in resp.text or "Team Management & Groups" in resp.text
    assert "QBIT SYSTEM // 34" in resp.text
    assert "Configured Teams" in resp.text
    assert "IAM Synchronized" in resp.text


async def test_admin_roles_page_renders_stitch_screen_35(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/admin/roles")
    assert resp.status_code == 200
    assert "Roles &amp; Permissions Matrix" in resp.text or "Roles & Permissions Matrix" in resp.text
    assert "QBIT SYSTEM // 35" in resp.text
    assert "Capability Matrix" in resp.text
    assert "CEO" in resp.text
    assert "ADMIN" in resp.text


async def test_admin_audit_page_renders_stitch_screen_36(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await ui_client.get("/admin/audit")
    assert resp.status_code == 200
    assert "Security Audit Logs &amp; Forensics" in resp.text or "Security Audit Logs & Forensics" in resp.text
    assert "QBIT SYSTEM // 36" in resp.text
    assert "Append-Only Immutability" in resp.text


async def test_job_detail_handles_running_job_naive_datetime(ui_client, db_session):
    import uuid
    from datetime import datetime
    from app.models.scrape import ScrapeJob, JobStatus

    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    job_id = uuid.uuid4()
    # Simulate SQLite naive datetime where tzinfo is None
    job = ScrapeJob(
        id=job_id,
        actor_id="website",
        actor_version="1.0.0",
        status=JobStatus.RUNNING.value,
        input={"url": "https://example.com"},
        started_at=datetime.now(),  # naive datetime
        completed_at=None,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await ui_client.get(f"/scraping/jobs/{job_id}")
    assert resp.status_code == 200
    assert "SCRAPE JOB" in resp.text
    assert "RUNNING" in resp.text


async def test_job_detail_handles_invalid_or_truncated_uuid(ui_client):
    await _login(ui_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    # 31-char truncated UUID as reported in incident
    resp = await ui_client.get("/scraping/jobs/a017f4c2-8c35-43da-b6f4-1edecdcd5a7", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/scraping/jobs"



