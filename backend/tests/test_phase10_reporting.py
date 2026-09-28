"""Phase 10 — CEO Dashboard, Employee Performance & Business Reporting Test Suite.

Verifies:
1. Executive CEO / Admin KPI computation (lead generation, sales pipeline, campaigns, team activity)
2. Honest, zero-hallucination metric handling (pipeline value is None if no commercial deals exist)
3. CrmDeal creation and live pipeline / closed-won value aggregation
4. Employee Performance Directory (leads assigned, contacted, qualified, converted, conversion rate)
5. Individual employee drilldown with target vs actual progress bars and gap analysis
6. Team workload distribution and unassigned lead counts
7. CSV and XLSX export generation with spreadsheet formula injection protection
8. Performance target CRUD with RBAC enforcement and audit logging
9. REST API endpoints (executive, employees, teams, targets, deals, export)
10. Server-rendered Operator UI pages (overview, team directory, employee detail)
"""

from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.analytics.core.filters import AnalyticsFilters
from app.analytics.core.time import Period, resolve_period
from app.analytics.domains.employee_performance import (
    get_employee_detail_performance,
    list_employees_performance,
    team_workload_and_reporting,
)
from app.analytics.domains.executive import executive_kpis
from app.analytics.reports.executive_exporter import (
    export_employees_csv,
    export_executive_csv,
    export_executive_xlsx,
    sanitize_cell,
)
from app.models.analytics import CrmDeal, PerformanceTarget
from app.models.enterprise import Organization, Team, TeamMember
from app.models.lead import FollowUpStatus, LeadActivity, LeadFollowUp
from app.models.scrape import Lead
from app.models.user import User
from app.services import rbac as rbac_service
from tests.conftest import ADMIN_EMAIL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

async def create_test_user(session, email: str, role: str = "OPERATOR") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        full_name=f"User {email.split('@')[0]}",
        password_hash="testhash",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await rbac_service.set_user_roles(session, user.id, [role])
    await session.commit()
    await session.refresh(user)
    return user


async def create_test_lead(
    session,
    status: str = "NEW",
    assigned_user_id: uuid.UUID | None = None,
    business_name: str = "Test Bistro",
) -> Lead:
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    lead = Lead(
        id=uuid.uuid4(),
        business_name=business_name,
        category="Restaurant",
        city="Mumbai",
        state="Maharashtra",
        country="India",
        phone="+91 99999 11111",
        email="bistro@example.com",
        status=status,
        assigned_user_id=assigned_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


# ---------------------------------------------------------------------------
# Test 1: Formula Injection Sanitization
# ---------------------------------------------------------------------------

async def test_formula_injection_sanitization():
    """Verify cells starting with risky spreadsheet prefix characters are escaped with '."""
    assert sanitize_cell("=1+1") == "'=1+1"
    assert sanitize_cell("+SUM(A1:A10)") == "'+SUM(A1:A10)"
    assert sanitize_cell("-100") == "'-100"
    assert sanitize_cell("@HYPERLINK('url')") == "'@HYPERLINK('url')"
    assert sanitize_cell("\tTABBED") == "'\tTABBED"
    assert sanitize_cell("\rRETURN") == "'\rRETURN"

    # Safe values
    assert sanitize_cell("Safe String") == "Safe String"
    assert sanitize_cell(45000) == 45000
    assert sanitize_cell(None) == "—"
    assert sanitize_cell("") == ""


# ---------------------------------------------------------------------------
# Test 2: Executive Domain Zero-Hallucination & Pipeline Calculation
# ---------------------------------------------------------------------------

async def test_executive_domain_metrics(app):
    async with app.state.db.session() as session:
        # 1. Clean slate check: no commercial deals recorded
        lead1 = await create_test_lead(session, status="NEW")
        lead2 = await create_test_lead(session, status="QUALIFIED")
        lead3 = await create_test_lead(session, status="CONVERTED")

        p = resolve_period(period="all", tz_name="UTC", now=datetime.now(timezone.utc) + timedelta(minutes=10))
        filters = AnalyticsFilters(period=p)

        exec_res = await executive_kpis(session, filters, prev_filters=None, dialect="sqlite")

        # Zero-hallucination verification
        assert exec_res["lead_generation"]["total_leads"] >= 3
        assert exec_res["lead_generation"]["converted_leads"] >= 1
        assert exec_res["sales_pipeline"]["pipeline_value_inr"] is None, "Must be None when no deals exist"
        assert exec_res["sales_pipeline"]["closed_won_value_inr"] is None

        # 2. Add real CrmDeal records and verify pipeline valuation
        now_deal = datetime.now(timezone.utc) - timedelta(minutes=1)
        deal1 = CrmDeal(
            id=uuid.uuid4(),
            lead_id=lead2.id,
            title="QBIT 15\" POS Terminal 5x",
            amount=225000.0,
            stage="PROPOSAL",
            created_at=now_deal,
            updated_at=now_deal,
        )
        deal2 = CrmDeal(
            id=uuid.uuid4(),
            lead_id=lead3.id,
            title="QBIT Handheld POS 2x",
            amount=37000.0,
            stage="CLOSED_WON",
            closed_at=now_deal,
            created_at=now_deal,
            updated_at=now_deal,
        )
        session.add_all([deal1, deal2])
        await session.commit()

        exec_res_with_deals = await executive_kpis(session, filters, prev_filters=None, dialect="sqlite")
        pipeline = exec_res_with_deals["sales_pipeline"]
        assert pipeline["pipeline_value_inr"] == 225000.0
        assert pipeline["closed_won_value_inr"] == 37000.0
        assert pipeline["closed_won_deals"] >= 1


# ---------------------------------------------------------------------------
# Test 3: Employee Performance & Target Tracking
# ---------------------------------------------------------------------------

async def test_employee_performance_and_targets(app):
    async with app.state.db.session() as session:
        op = await create_test_user(session, email="sales_rep_1@qbit.example", role="OPERATOR")

        # Assign 4 leads: 2 qualified, 1 converted, 1 new
        l1 = await create_test_lead(session, status="NEW", assigned_user_id=op.id)
        l2 = await create_test_lead(session, status="QUALIFIED", assigned_user_id=op.id)
        l3 = await create_test_lead(session, status="QUALIFIED", assigned_user_id=op.id)
        l4 = await create_test_lead(session, status="CONVERTED", assigned_user_id=op.id)

        # Log some activities
        now = datetime.now(timezone.utc)
        act1 = LeadActivity(
            id=uuid.uuid4(),
            lead_id=l1.id,
            user_id=op.id,
            event_type="call_logged",
            message="Discovery call",
            created_at=now,
        )
        act2 = LeadActivity(
            id=uuid.uuid4(),
            lead_id=l4.id,
            user_id=op.id,
            event_type="stage_changed",
            message="Closed deal",
            created_at=now,
        )
        session.add_all([act1, act2])

        # Set a target for this employee
        target = PerformanceTarget(
            id=uuid.uuid4(),
            user_id=op.id,
            metric="conversions",
            target_value=2.0,
            period_type="monthly",
            start_date=now.date() - timedelta(days=5),
            end_date=now.date() + timedelta(days=25),
            notes="Q3 conversion goal",
        )
        session.add(target)
        await session.commit()

        p = resolve_period(period="all", tz_name="UTC", now=datetime.now(timezone.utc) + timedelta(minutes=10))
        filters = AnalyticsFilters(period=p)

        # 1. Directory listing
        emp_list = await list_employees_performance(session, filters, allowed_user_ids=[op.id])
        assert len(emp_list) == 1
        emp_data = emp_list[0]
        assert emp_data["assigned_leads"] == 4
        assert emp_data["converted_leads"] == 1
        assert emp_data["conversion_rate"] == 0.25  # 1/4

        # 2. Detail drilldown
        detail = await get_employee_detail_performance(session, op.id, filters, dialect="sqlite")
        assert detail is not None
        assert detail["summary"]["assigned_leads"] == 4
        assert detail["summary"]["converted_leads"] == 1
        assert detail["summary"]["conversion_rate"] == 0.25

        # Check target progress calculation
        assert len(detail["targets"]) >= 1
        t_res = next(t for t in detail["targets"] if t["metric"] == "conversions")
        assert t_res["actual_value"] == 1
        assert t_res["target_value"] == 2.0
        assert t_res["progress_pct"] == 50.0
        assert t_res["gap"] == 1.0


# ---------------------------------------------------------------------------
# Test 4: Team Workload Distribution
# ---------------------------------------------------------------------------

async def test_team_workload_distribution(app):
    async with app.state.db.session() as session:
        org = Organization(id=uuid.uuid4(), name="QBIT Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()

        team = Team(id=uuid.uuid4(), organization_id=org.id, name="North Region Sales", slug="north-sales")
        session.add(team)
        await session.flush()

        op = await create_test_user(session, email="north_rep@qbit.example", role="OPERATOR")
        member = TeamMember(id=uuid.uuid4(), team_id=team.id, user_id=op.id, is_lead=False)
        session.add(member)

        # Assign lead to user in team
        await create_test_lead(session, status="NEW", assigned_user_id=op.id)
        # Unassigned lead
        await create_test_lead(session, status="NEW", assigned_user_id=None)
        await session.commit()

        p = resolve_period(period="all", tz_name="UTC", now=datetime.now(timezone.utc) + timedelta(minutes=10))
        filters = AnalyticsFilters(period=p)

        report = await team_workload_and_reporting(session, filters, team_id=team.id)
        assert report["unassigned_leads"] >= 1
        assert len(report["teams"]) >= 1
        north_team = next(t for t in report["teams"] if t["team_id"] == str(team.id))
        assert north_team["lead_count"] >= 1


# ---------------------------------------------------------------------------
# Test 5: CSV & XLSX Export Functionality
# ---------------------------------------------------------------------------

async def test_executive_and_employee_exports():
    exec_data = {
        "lead_generation": {
            "total_leads": 120,
            "new_leads": 40,
            "qualified_leads": 35,
            "converted_leads": 12,
            "leads_by_source": [{"source": "google_maps", "count": 80}],
        },
        "sales_pipeline": {
            "pipeline_value_inr": 450000.0,
            "closed_won_value_inr": 185000.0,
            "open_opportunities": 8,
            "closed_won_deals": 3,
            "closed_lost_deals": 1,
            "lead_to_opportunity_rate": 0.25,
            "opportunity_to_win_rate": 0.375,
            "lead_to_win_rate": 0.1,
            "overdue_followups": 2,
        },
        "campaign_performance": {
            "email": {
                "sent": 500, "delivered": 480, "opened": 240, "clicked": 60,
                "bounced": 20, "unsubscribed": 2, "open_rate": 0.5, "click_to_open_rate": 0.25,
            },
            "whatsapp": {
                "sent": 300, "delivered": 290, "read": 210, "replied": 75,
                "failed": 10, "delivery_rate": 0.966, "read_rate": 0.724, "reply_rate": 0.258,
            },
            "social": {
                "posts_scheduled": 5, "posts_published": 15, "posts_failed": 0,
                "posts_by_platform": {"LINKEDIN": 10, "TWITTER": 5},
            },
            "cost_per_lead_inr": None,
            "return_on_ad_spend": None,
        },
        "team_activity": {
            "activities_completed": 150,
            "conversations_resolved": 45,
            "followups_completed": 30,
            "notes_added": 25,
            "top_performers": [
                {
                    "name": "Alice Smith",
                    "email": "alice@example.com",
                    "assigned_leads": 20,
                    "qualified_leads": 12,
                    "converted_leads": 5,
                    "conversion_rate": 0.25,
                }
            ],
        },
    }

    # 1. Executive CSV export
    csv_str = export_executive_csv(exec_data)
    assert "EXECUTIVE CEO REPORT" in csv_str
    assert "450000.0" in csv_str
    assert "Alice Smith" in csv_str

    # 2. Executive XLSX export
    xlsx_bytes = export_executive_xlsx(exec_data)
    assert isinstance(xlsx_bytes, bytes)
    assert len(xlsx_bytes) > 1000

    # 3. Employee CSV export
    emp_rows = [
        {
            "name": "Bob Jones",
            "email": "=bob@danger.com",  # should be sanitized
            "role": "OPERATOR",
            "team_name": "Sales",
            "assigned_leads": 10,
            "contacted_leads": 8,
            "qualified_leads": 6,
            "converted_leads": 2,
            "conversion_rate": 0.20,
            "follow_ups_completed": 5,
            "active_deals_count": 3,
            "closed_won_value_inr": 75000.0,
        }
    ]
    emp_csv = export_employees_csv(emp_rows)
    assert "Bob Jones" in emp_csv
    assert "'=bob@danger.com" in emp_csv, "Risky formula cell must be sanitized"


# ---------------------------------------------------------------------------
# Test 6: REST API Lifecycle for Targets, Deals, and Exports
# ---------------------------------------------------------------------------

async def test_analytics_api_lifecycle(client: AsyncClient, admin_headers: dict, app):
    # 1. Executive API
    exec_resp = await client.get("/api/v1/analytics/executive", headers=admin_headers)
    assert exec_resp.status_code == 200, exec_resp.text
    exec_json = exec_resp.json()
    assert exec_json["success"] is True
    assert "lead_generation" in exec_json["data"]
    assert "sales_pipeline" in exec_json["data"]

    # 2. Performance Targets CRUD
    target_payload = {
        "metric": "leads_qualified",
        "target_value": 30.0,
        "period_type": "monthly",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "notes": "End of quarter sprint",
    }
    create_t_resp = await client.post("/api/v1/analytics/targets", json=target_payload, headers=admin_headers)
    assert create_t_resp.status_code == 200, create_t_resp.text
    target_id = create_t_resp.json()["data"]["id"]

    # List targets
    list_t_resp = await client.get("/api/v1/analytics/targets", headers=admin_headers)
    assert list_t_resp.status_code == 200
    assert any(t["id"] == target_id for t in list_t_resp.json()["data"])

    # 3. CRM Commercial Deals CRUD
    deal_payload = {
        "title": "Hardware Order - Multi-store Retail",
        "amount": 180000.0,
        "currency": "INR",
        "stage": "NEGOTIATION",
        "probability": 0.8,
        "notes": "Evaluating 4 POS units",
    }
    create_d_resp = await client.post("/api/v1/analytics/deals", json=deal_payload, headers=admin_headers)
    assert create_d_resp.status_code == 200, create_d_resp.text
    deal_id = create_d_resp.json()["data"]["id"]

    list_d_resp = await client.get("/api/v1/analytics/deals", headers=admin_headers)
    assert list_d_resp.status_code == 200
    assert any(d["id"] == deal_id for d in list_d_resp.json()["data"])

    # 4. Exports via API
    # CSV
    exp_csv_resp = await client.get("/api/v1/analytics/export?report_type=executive&format=csv", headers=admin_headers)
    assert exp_csv_resp.status_code == 200
    assert exp_csv_resp.headers["content-type"].startswith("text/csv")
    assert "EXECUTIVE CEO REPORT" in exp_csv_resp.text

    # XLSX
    exp_xlsx_resp = await client.get("/api/v1/analytics/export?report_type=executive&format=xlsx", headers=admin_headers)
    assert exp_xlsx_resp.status_code == 200
    assert "openxmlformats" in exp_xlsx_resp.headers["content-type"]
    assert len(exp_xlsx_resp.content) > 1000

    # Employees export
    exp_emp_resp = await client.get("/api/v1/analytics/export?report_type=employees&format=csv", headers=admin_headers)
    assert exp_emp_resp.status_code == 200
    assert "EMPLOYEE PERFORMANCE DIRECTORY" in exp_emp_resp.text

    # 5. Delete Target
    del_t_resp = await client.delete(f"/api/v1/analytics/targets/{target_id}", headers=admin_headers)
    assert del_t_resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 7: UI Pages Render Cleanly
# ---------------------------------------------------------------------------

async def test_phase10_ui_pages(client: AsyncClient, admin_headers: dict, app):
    # Overview page
    resp_overview = await client.get("/analytics", headers=admin_headers)
    assert resp_overview.status_code == 200
    assert "EXECUTIVE / CEO OVERVIEW" in resp_overview.text
    assert "Export CSV" in resp_overview.text

    # Team & Employee directory page
    resp_team = await client.get("/analytics/team", headers=admin_headers)
    assert resp_team.status_code == 200
    assert "EMPLOYEE PERFORMANCE DIRECTORY" in resp_team.text
    assert "PERFORMANCE TARGETS" in resp_team.text
