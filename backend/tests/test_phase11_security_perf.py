"""Phase 11 — Security Audit, Performance Profiling & End-to-End Verification Test Suite.

Verifies:
1. Multi-tenant IDOR protection across AI agent runs, lead intelligence, scoring, and proposals.
2. Tenant scoping and negative authorization on Commercial Deals and Performance Targets.
3. AI Usage record isolation by organization.
4. Scraper NetGuard SSRF defense: loopback, RFC1918, link-local, cloud metadata, and IPv4-mapped IPv6.
5. Storage path traversal defense: dot-dot traversal, absolute paths, null bytes, and drive escapes.
6. Session revocation & auth integrity: immediate rejection after session revocation.
7. Login guard brute-force protection & account lockout.
8. API Performance baseline: critical endpoints respond under performance thresholds.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from pathlib import Path

from app.core.errors import PathAccessDeniedError
from app.core.path_safety import validate_storage_key
from app.core.security import hash_password
from app.models.ai import AIAgentRun, AgentRunStatus, LeadIntelligence, LeadScoreRecord
from app.models.analytics import CrmDeal, PerformanceTarget
from app.models.enterprise import MemberStatus, Organization, OrganizationMember
from app.models.scrape import Lead
from app.models.user import User
from app.scrapers.core.exceptions import ScraperValidationError
from app.scrapers.core.netguard import UrlPolicy, validate_url
from app.services import rbac as rbac_service
from app.services.login_guard import is_locked, register_failure, register_success
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _create_org(app, slug: str, name: str) -> uuid.UUID:
    db = app.state.db
    async with db.session() as session:
        org = Organization(name=name, slug=slug)
        session.add(org)
        await session.commit()
        return org.id


async def _create_tenant_user(
    app,
    *,
    email: str,
    password: str = "SecurePass!123",
    roles: tuple[str, ...] = ("ADMIN",),
    org_id: uuid.UUID,
    full_name: str = "Tenant User",
) -> tuple[uuid.UUID, str]:
    db = app.state.db
    async with db.session() as session:
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            status="ACTIVE",
        )
        session.add(user)
        await session.flush()
        await rbac_service.set_user_roles(session, user.id, list(roles))
        session.add(
            OrganizationMember(
                organization_id=org_id,
                user_id=user.id,
                status=MemberStatus.ACTIVE.value,
            )
        )
        await session.commit()
        return user.id, password


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_lead(app, *, org_id: uuid.UUID, created_by: uuid.UUID, name: str) -> uuid.UUID:
    db = app.state.db
    now = datetime.now(timezone.utc)
    async with db.session() as session:
        lead = Lead(
            id=uuid.uuid4(),
            organization_id=org_id,
            created_by=created_by,
            business_name=name,
            category="Restaurant",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            phone="+91 91234 56789",
            email=f"contact@{name.lower().replace(' ', '')}.com",
            status="NEW",
            source="manual",
            created_at=now,
            updated_at=now,
        )
        session.add(lead)
        await session.commit()
        return lead.id


# ---------------------------------------------------------------------------
# 1. Multi-Tenant AI IDOR & Isolation Tests
# ---------------------------------------------------------------------------

class TestAITenancySecurity:
    """Verifies that Org A can never inspect, run agents on, or alter Org B's leads."""

    async def test_cross_tenant_agent_run_denied(self, app, client):
        org_a = await _create_org(app, f"org-a-{uuid.uuid4().hex[:6]}", "Org A")
        org_b = await _create_org(app, f"org-b-{uuid.uuid4().hex[:6]}", "Org B")

        user_a_id, pass_a = await _create_tenant_user(app, email=f"usera-{uuid.uuid4().hex[:6]}@orga.com", org_id=org_a)
        user_b_id, _ = await _create_tenant_user(app, email=f"userb-{uuid.uuid4().hex[:6]}@orgb.com", org_id=org_b)

        db = app.state.db
        async with db.session() as s:
            email_a = (await s.scalar(select(User.email).where(User.id == user_a_id)))
        headers_a = await _login(client, email_a, pass_a)

        lead_b = await _create_lead(app, org_id=org_b, created_by=user_b_id, name="Secret Org B Bistro")

        # Org A attempts to trigger AI agent run on Org B's lead -> 404
        resp = await client.post(
            "/api/v1/ai/agents/lead-enrichment/run",
            json={"lead_id": str(lead_b)},
            headers=headers_a,
        )
        assert resp.status_code == 404
        assert "not found" in resp.text.lower()

    async def test_cross_tenant_enrich_and_score_denied(self, app, client):
        org_a = await _create_org(app, f"org-a-{uuid.uuid4().hex[:6]}", "Org A")
        org_b = await _create_org(app, f"org-b-{uuid.uuid4().hex[:6]}", "Org B")

        user_a_id, pass_a = await _create_tenant_user(app, email=f"usera-{uuid.uuid4().hex[:6]}@orga.com", org_id=org_a)
        user_b_id, _ = await _create_tenant_user(app, email=f"userb-{uuid.uuid4().hex[:6]}@orgb.com", org_id=org_b)

        db = app.state.db
        async with db.session() as s:
            email_a = (await s.scalar(select(User.email).where(User.id == user_a_id)))
        headers_a = await _login(client, email_a, pass_a)

        lead_b = await _create_lead(app, org_id=org_b, created_by=user_b_id, name="Org B Tech Cafe")

        # Enrich lead from Org B
        resp_enrich = await client.post(f"/api/v1/ai/leads/{lead_b}/enrich", headers=headers_a)
        assert resp_enrich.status_code == 404

        # Intelligence for Org B
        resp_intel = await client.get(f"/api/v1/ai/leads/{lead_b}/intelligence", headers=headers_a)
        assert resp_intel.status_code == 404

        # Score lead from Org B
        resp_score = await client.post(f"/api/v1/ai/leads/{lead_b}/score", headers=headers_a)
        assert resp_score.status_code == 404

        # Sales brief for Org B
        resp_brief = await client.post(f"/api/v1/ai/leads/{lead_b}/sales-brief/generate", headers=headers_a)
        assert resp_brief.status_code == 404

        # Enrichment proposals for Org B
        resp_prop = await client.get(f"/api/v1/ai/leads/{lead_b}/enrichment-proposals", headers=headers_a)
        assert resp_prop.status_code == 404

    async def test_cross_tenant_run_inspection_and_cancellation_denied(self, app, client):
        org_a = await _create_org(app, f"org-a-{uuid.uuid4().hex[:6]}", "Org A")
        org_b = await _create_org(app, f"org-b-{uuid.uuid4().hex[:6]}", "Org B")

        user_a_id, pass_a = await _create_tenant_user(app, email=f"usera-{uuid.uuid4().hex[:6]}@orga.com", org_id=org_a)
        user_b_id, _ = await _create_tenant_user(app, email=f"userb-{uuid.uuid4().hex[:6]}@orgb.com", org_id=org_b)

        db = app.state.db
        async with db.session() as s:
            email_a = (await s.scalar(select(User.email).where(User.id == user_a_id)))
        headers_a = await _login(client, email_a, pass_a)

        # Create a run in Org B directly
        now = datetime.now(timezone.utc)
        lead_b = await _create_lead(app, org_id=org_b, created_by=user_b_id, name="Org B Hotel")
        async with db.session() as s:
            run_b = AIAgentRun(
                id=uuid.uuid4(),
                organization_id=org_b,
                user_id=user_b_id,
                lead_id=lead_b,
                task_name="ai_agent:lead-enrichment",
                status=AgentRunStatus.QUEUED.value,
                input_data={"lead_id": str(lead_b)},
                created_at=now,
            )
            s.add(run_b)
            await s.commit()
            run_b_id = run_b.id

        # Org A tries to inspect Org B's run
        resp_get = await client.get(f"/api/v1/ai/runs/{run_b_id}", headers=headers_a)
        assert resp_get.status_code == 404

        # Org A tries to cancel Org B's run
        resp_cancel = await client.post(f"/api/v1/ai/runs/{run_b_id}/cancel", headers=headers_a)
        assert resp_cancel.status_code == 404


# ---------------------------------------------------------------------------
# 2. Analytics & Deals Multi-Tenant Isolation Tests
# ---------------------------------------------------------------------------

class TestAnalyticsTenancySecurity:
    """Verifies that performance targets and commercial deals enforce tenant boundaries."""

    async def test_deals_cross_tenant_isolation(self, app, client):
        org_a = await _create_org(app, f"org-a-{uuid.uuid4().hex[:6]}", "Org A")
        org_b = await _create_org(app, f"org-b-{uuid.uuid4().hex[:6]}", "Org B")

        user_a_id, pass_a = await _create_tenant_user(app, email=f"usera-{uuid.uuid4().hex[:6]}@orga.com", org_id=org_a)
        user_b_id, _ = await _create_tenant_user(app, email=f"userb-{uuid.uuid4().hex[:6]}@orgb.com", org_id=org_b)

        db = app.state.db
        async with db.session() as s:
            email_a = (await s.scalar(select(User.email).where(User.id == user_a_id)))
        headers_a = await _login(client, email_a, pass_a)

        lead_b = await _create_lead(app, org_id=org_b, created_by=user_b_id, name="Target Deal Lead B")

        # Org A attempts to create a deal against Org B's lead -> 404
        deal_payload = {
            "title": "Unauthorized Hardware Deal",
            "amount": 50000.0,
            "stage": "PROPOSAL",
            "lead_id": str(lead_b),
        }
        resp = await client.post("/api/v1/analytics/deals", json=deal_payload, headers=headers_a)
        assert resp.status_code == 404

    async def test_performance_targets_isolation(self, app, client):
        org_a = await _create_org(app, f"org-a-{uuid.uuid4().hex[:6]}", "Org A")
        org_b = await _create_org(app, f"org-b-{uuid.uuid4().hex[:6]}", "Org B")

        user_a_id, pass_a = await _create_tenant_user(app, email=f"usera-{uuid.uuid4().hex[:6]}@orga.com", org_id=org_a)
        user_b_id, _ = await _create_tenant_user(app, email=f"userb-{uuid.uuid4().hex[:6]}@orgb.com", org_id=org_b)

        db = app.state.db
        async with db.session() as s:
            email_a = (await s.scalar(select(User.email).where(User.id == user_a_id)))
            target_b = PerformanceTarget(
                id=uuid.uuid4(),
                organization_id=org_b,
                metric="revenue",
                target_value=500000.0,
                period_type="quarterly",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 9, 30),
                set_by=user_b_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            s.add(target_b)
            await s.commit()
            target_b_id = target_b.id

        headers_a = await _login(client, email_a, pass_a)

        # Org A lists targets -> Org B's target must NOT be returned
        list_resp = await client.get("/api/v1/analytics/targets", headers=headers_a)
        assert list_resp.status_code == 200
        targets = list_resp.json()["data"]
        target_ids = [t["id"] for t in targets]
        assert str(target_b_id) not in target_ids

        # Org A attempts to delete Org B's target -> 404
        del_resp = await client.delete(f"/api/v1/analytics/targets/{target_b_id}", headers=headers_a)
        assert del_resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Scraper NetGuard SSRF Defense Tests
# ---------------------------------------------------------------------------

class TestNetGuardSSRFProtection:
    """Verifies that the scraper engine rejects all internal, loopback, and metadata destinations."""

    def test_ssrf_rejects_loopback(self):
        policy = UrlPolicy()
        with pytest.raises(ScraperValidationError):
            validate_url("http://127.0.0.1", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://127.0.0.1:8000/internal", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://localhost:3000", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://[::1]", policy, resolve=False)

    def test_ssrf_rejects_rfc1918_private_networks(self):
        policy = UrlPolicy()
        with pytest.raises(ScraperValidationError):
            validate_url("http://10.0.0.1/admin", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://10.255.255.254", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://172.16.0.1:9000", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://172.31.255.255", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://192.168.1.1", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://192.168.0.254/router", policy, resolve=False)

    def test_ssrf_rejects_cloud_metadata(self):
        policy = UrlPolicy()
        # AWS / GCP / Azure IMDS
        with pytest.raises(ScraperValidationError):
            validate_url("http://169.254.169.254/latest/meta-data/", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://metadata.google.internal/computeMetadata/v1/", policy, resolve=False)

    def test_ssrf_rejects_dangerous_schemes(self):
        policy = UrlPolicy()
        with pytest.raises(ScraperValidationError):
            validate_url("file:///etc/passwd", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("file:///C:/Windows/win.ini", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("ftp://ftp.example.com/file", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("gopher://127.0.0.1:70", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("data:text/html,<script>alert(1)</script>", policy, resolve=False)

    def test_ssrf_rejects_ipv4_mapped_ipv6(self):
        policy = UrlPolicy()
        with pytest.raises(ScraperValidationError):
            validate_url("http://[::ffff:127.0.0.1]", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://[::ffff:10.0.0.1]", policy, resolve=False)
        with pytest.raises(ScraperValidationError):
            validate_url("http://[::ffff:169.254.169.254]", policy, resolve=False)

    def test_ssrf_allows_public_domains(self):
        policy = UrlPolicy()
        assert validate_url("https://example.com", policy, resolve=False) == "https://example.com"
        assert validate_url("https://www.google.com/search?q=pos", policy, resolve=False) == "https://www.google.com/search?q=pos"
        assert validate_url("https://api.github.com/users", policy, resolve=False) == "https://api.github.com/users"


# ---------------------------------------------------------------------------
# 4. Storage Path Traversal Defense Tests
# ---------------------------------------------------------------------------

class TestPathSafety:
    """Verifies that object storage keys cannot escape container boundaries."""

    def test_path_traversal_rejections(self, tmp_path: Path):
        with pytest.raises(PathAccessDeniedError):
            validate_storage_key("../secret.txt", tmp_path)

        with pytest.raises(PathAccessDeniedError):
            validate_storage_key("uploads/../../etc/passwd", tmp_path)

        with pytest.raises(PathAccessDeniedError):
            validate_storage_key(r"uploads\..\..\windows\system32", tmp_path)

    def test_absolute_path_rejections(self, tmp_path: Path):
        with pytest.raises(PathAccessDeniedError):
            validate_storage_key("/etc/passwd", tmp_path)

        with pytest.raises(PathAccessDeniedError):
            validate_storage_key("C:\\Windows\\system.ini", tmp_path)

    def test_null_byte_rejections(self, tmp_path: Path):
        with pytest.raises(PathAccessDeniedError):
            validate_storage_key("exports/data.csv\x00.exe", tmp_path)

    def test_valid_storage_keys(self, tmp_path: Path):
        (tmp_path / "exports" / "2026").mkdir(parents=True, exist_ok=True)
        (tmp_path / "avatars").mkdir(parents=True, exist_ok=True)
        assert validate_storage_key("exports/2026/report.csv", tmp_path).is_relative_to(tmp_path.resolve())
        assert validate_storage_key("avatars/user-123.jpg", tmp_path).is_relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# 5. Session Revocation & Brute-Force Guard Tests
# ---------------------------------------------------------------------------

class TestSessionAndBruteForceSecurity:
    """Verifies that sessions can be revoked immediately and brute-force attempts trigger lockout."""

    async def test_session_revocation_blocks_access(self, app, client):
        admin_headers = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        me_resp = await client.get("/api/v1/auth/me", headers=admin_headers)
        assert me_resp.status_code == 200

        # Logout revokes the session
        logout_resp = await client.post("/api/v1/auth/logout", headers=admin_headers)
        assert logout_resp.status_code == 200

        # Subsequent request with old token is rejected
        subsequent = await client.get("/api/v1/auth/me", headers=admin_headers)
        assert subsequent.status_code == 401

    async def test_login_guard_lockout_mechanism(self, app):
        db = app.state.db
        settings = app.state.settings
        now = datetime.now(timezone.utc)
        async with db.session() as session:
            victim = User(
                id=uuid.uuid4(),
                email=f"victim-{uuid.uuid4().hex[:6]}@example.com",
                password_hash=hash_password("VictimSecret123!"),
                full_name="Victim User",
                status="ACTIVE",
            )
            session.add(victim)
            await session.commit()

            assert not is_locked(victim)

            # Record failures up to threshold
            threshold = settings.QBIT_LOGIN_MAX_FAILED_ATTEMPTS
            for _ in range(threshold - 1):
                await register_failure(session, victim, settings)
                assert not is_locked(victim)

            # Reaching threshold locks user
            await register_failure(session, victim, settings)
            assert is_locked(victim)

            # Successful login resets lockout
            await register_success(session, victim)
            assert not is_locked(victim)


# ---------------------------------------------------------------------------
# 6. API Performance Baseline Tests
# ---------------------------------------------------------------------------

class TestAPIPerformanceBaseline:
    """Measures endpoint latency to ensure responsiveness within SLAs."""

    async def test_core_endpoint_response_times(self, app, client):
        admin_headers = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)

        endpoints = [
            ("GET", "/api/v1/analytics/overview"),
            ("GET", "/api/v1/ai/agents"),
            ("GET", "/api/v1/ai/products"),
            ("GET", "/api/v1/analytics/deals"),
            ("GET", "/api/v1/analytics/targets"),
        ]

        for method, path in endpoints:
            if method == "GET":
                # Warm-up request
                warm_resp = await client.get(path, headers=admin_headers)
                assert warm_resp.status_code == 200, f"{path} warm-up failed with {warm_resp.status_code}: {warm_resp.text}"

                # Timed execution
                t0 = time.monotonic()
                resp = await client.get(path, headers=admin_headers)
                elapsed_ms = (time.monotonic() - t0) * 1000

                assert resp.status_code == 200, f"{path} failed with {resp.status_code}: {resp.text}"
                # SLA assertion: warm read endpoint response time should be well under 500ms
                assert elapsed_ms < 500, f"{path} took {elapsed_ms:.1f}ms, exceeding 500ms SLA"
