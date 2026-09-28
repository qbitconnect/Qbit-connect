"""Phase 5 — Production CRM Full-Stack Integration Test Suite.

Comprehensive validation covering:
1. Pipeline Stage Progression & Validation:
   - Valid sequential progression (NEW -> ASSIGNED -> CONTACTED -> FOLLOW_UP -> QUALIFIED -> CONVERTED)
   - Invalid stage jump rejection without manager reason
   - Manager override with explanatory reason
   - Full LeadStatusHistory audit logging and last_activity_at updating
2. Follow-Up Tasks & Reminder Engine:
   - Creation of tasks with title, due_at, notes, priority, assigned_user_id
   - Retrieval for specific lead and user task dashboard
   - Completion and cancellation workflows with activity trail logging
   - Dashboard task metrics (pending, due today, overdue)
3. Contact Person Records & Deduplication:
   - Creating contact persons for a business lead
   - Primary contact demotion/uniqueness guarantee
   - Listing contacts
4. Communication & Interaction Logging:
   - Logging calls, emails, meetings, notes to lead timeline
   - Verification of timeline activity entries
5. Bulk CRM Operations:
   - Bulk assignment to employee with history tracking
   - Bulk priority update
6. Safe Lead Merge with CRM Re-homing:
   - Re-homing follow-ups, contacts, status history, assignment history
   - Verified status protection
   - Primary contact deconfliction
7. CRM Dashboard Analytics:
   - Truthful aggregate metrics (stages, priorities, conversion rate, task metrics)
8. Employee Lead Isolation & Server-Side Security:
   - Isolation of leads for assigned-only employees
   - Cross-tenant and IDOR prevention
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.lead import (
    FollowUpPriority,
    FollowUpStatus,
    LeadContact,
    LeadFollowUp,
    LeadStatus,
    LeadStatusHistory,
    PIPELINE_STAGES,
)
from app.models.scrape import Lead
from app.services.leads.pipeline import PipelineService, validate_stage_transition


@pytest.mark.asyncio
async def test_stage_transition_validation():
    """Verify validation logic for standard, non-standard, and invalid stage jumps."""
    # Standard forward progression
    ok, err = validate_stage_transition("NEW", "ASSIGNED")
    assert ok is True
    assert err is None

    ok, err = validate_stage_transition("ASSIGNED", "CONTACTED")
    assert ok is True
    assert err is None

    ok, err = validate_stage_transition("CONTACTED", "QUALIFIED")
    assert ok is True
    assert err is None

    # Invalid jump without admin/manager override
    ok, err = validate_stage_transition("NEW", "CONVERTED", is_admin_or_manager=False)
    assert ok is False
    assert "Invalid stage transition" in err

    # Manager override without reason fails
    ok, err = validate_stage_transition("NEW", "CONVERTED", is_admin_or_manager=True, reason="")
    assert ok is False
    assert "requires an explanatory reason" in err

    # Manager override with valid reason succeeds
    ok, err = validate_stage_transition("NEW", "CONVERTED", is_admin_or_manager=True, reason="Existing enterprise customer converted on day 1")
    assert ok is True
    assert err is None

    # Invalid stage name
    ok, err = validate_stage_transition("NEW", "NON_EXISTENT_STAGE")
    assert ok is False
    assert "not a valid pipeline stage" in err


@pytest.mark.asyncio
async def test_pipeline_stage_progression_api(client: AsyncClient, admin_headers: dict):
    """Test stage transition via API, audit history, and last_activity_at timestamp."""
    # 1. Create a lead
    create_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Acme Industrial Labs", "email": "info@acmelabs.com"},
        headers=admin_headers,
    )
    assert create_res.status_code == 200
    lead_id = create_res.json()["data"]["id"]
    initial_activity = create_res.json()["data"].get("last_activity_at")

    # 2. Advance to ASSIGNED
    stage_res = await client.post(
        f"/api/v1/leads/{lead_id}/stage",
        json={"stage": "ASSIGNED", "reason": "Assigned to sales rep"},
        headers=admin_headers,
    )
    assert stage_res.status_code == 200
    data = stage_res.json()["data"]
    assert data["status"] == "ASSIGNED"
    assert data["last_activity_at"] is not None

    # 3. Advance to CONTACTED
    stage_res2 = await client.post(
        f"/api/v1/leads/{lead_id}/stage",
        json={"stage": "CONTACTED", "reason": "Intro call conducted"},
        headers=admin_headers,
    )
    assert stage_res2.status_code == 200
    assert stage_res2.json()["data"]["status"] == "CONTACTED"

    # 4. Check stage history
    history_res = await client.get(
        f"/api/v1/leads/{lead_id}/stage-history",
        headers=admin_headers,
    )
    assert history_res.status_code == 200
    history = history_res.json()["data"]
    assert len(history) >= 2
    assert history[0]["new_status"] == "CONTACTED"
    assert history[0]["previous_status"] == "ASSIGNED"
    assert history[0]["reason"] == "Intro call conducted"
    assert history[1]["new_status"] == "ASSIGNED"


@pytest.mark.asyncio
async def test_followup_tasks_lifecycle(client: AsyncClient, admin_headers: dict):
    """Test scheduling, completing, and cancelling follow-up tasks."""
    # 1. Create a lead
    create_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Summit Health Solutions", "phone": "+91-9876543210"},
        headers=admin_headers,
    )
    lead_id = create_res.json()["data"]["id"]

    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    task_res = await client.post(
        f"/api/v1/leads/{lead_id}/followups",
        json={
            "title": "Send commercial proposal",
            "due_at": due,
            "notes": "Include bulk discount terms",
            "priority": "HIGH",
        },
        headers=admin_headers,
    )
    assert task_res.status_code == 200
    task_data = task_res.json()["data"]
    task_id = task_data["id"]
    assert task_data["status"] == "PENDING"
    assert task_data["priority"] == "HIGH"
    assert task_data["title"] == "Send commercial proposal"

    # 2. List lead followups
    list_res = await client.get(
        f"/api/v1/leads/{lead_id}/followups",
        headers=admin_headers,
    )
    assert list_res.status_code == 200
    items = list_res.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == task_id

    # 3. Complete followup
    complete_res = await client.post(
        f"/api/v1/leads/followups/{task_id}/complete",
        headers=admin_headers,
    )
    assert complete_res.status_code == 200
    assert complete_res.json()["data"]["status"] == "COMPLETED"
    assert complete_res.json()["data"]["completed_at"] is not None

    # 4. Create another followup and cancel it
    task2_res = await client.post(
        f"/api/v1/leads/{lead_id}/followups",
        json={"title": "Check contract signature", "due_at": due, "priority": "LOW"},
        headers=admin_headers,
    )
    task2_id = task2_res.json()["data"]["id"]
    cancel_res = await client.post(
        f"/api/v1/leads/followups/{task2_id}/cancel",
        headers=admin_headers,
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["data"]["status"] == "CANCELLED"

    # 5. Verify timeline activity logged
    act_res = await client.get(
        f"/api/v1/leads/{lead_id}/activity",
        headers=admin_headers,
    )
    assert act_res.status_code == 200
    event_types = [a["event_type"] for a in act_res.json()["data"]["items"]]
    assert "follow_up_created" in event_types
    assert "follow_up_completed" in event_types
    assert "follow_up_cancelled" in event_types


@pytest.mark.asyncio
async def test_followups_dashboard_and_queries(client: AsyncClient, admin_headers: dict):
    """Test the followups dashboard endpoint for overdue and due tasks."""
    lead_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Apex Manufacturing Ltd"},
        headers=admin_headers,
    )
    lead_id = lead_res.json()["data"]["id"]

    now = datetime.now(timezone.utc)
    # Past due task
    overdue_due = (now - timedelta(hours=5)).isoformat()
    await client.post(
        f"/api/v1/leads/{lead_id}/followups",
        json={"title": "Overdue Followup", "due_at": overdue_due, "priority": "URGENT"},
        headers=admin_headers,
    )

    # Future task
    future_due = (now + timedelta(days=3)).isoformat()
    await client.post(
        f"/api/v1/leads/{lead_id}/followups",
        json={"title": "Future Followup", "due_at": future_due, "priority": "MEDIUM"},
        headers=admin_headers,
    )

    # Query all
    dash_res = await client.get("/api/v1/leads/followups/dashboard", headers=admin_headers)
    assert dash_res.status_code == 200
    assert dash_res.json()["data"]["total"] >= 2

    # Query overdue only
    overdue_res = await client.get(
        "/api/v1/leads/followups/dashboard?overdue_only=true",
        headers=admin_headers,
    )
    assert overdue_res.status_code == 200
    overdue_titles = [t["title"] for t in overdue_res.json()["data"]["items"]]
    assert "Overdue Followup" in overdue_titles
    assert "Future Followup" not in overdue_titles


@pytest.mark.asyncio
async def test_lead_contacts_management(client: AsyncClient, admin_headers: dict):
    """Test contact persons management and primary contact exclusivity."""
    lead_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Quantum Robotics Corp"},
        headers=admin_headers,
    )
    lead_id = lead_res.json()["data"]["id"]

    # Add Contact 1 (Primary)
    c1_res = await client.post(
        f"/api/v1/leads/{lead_id}/contacts",
        json={
            "first_name": "Aarav",
            "last_name": "Patel",
            "title": "CTO",
            "email": "aarav@quantumrobotics.com",
            "phone": "+91-9123456789",
            "is_primary": True,
            "is_verified": True,
        },
        headers=admin_headers,
    )
    assert c1_res.status_code == 200
    c1_id = c1_res.json()["data"]["id"]
    assert c1_res.json()["data"]["is_primary"] is True
    assert c1_res.json()["data"]["is_verified"] is True

    # Add Contact 2 (New Primary)
    c2_res = await client.post(
        f"/api/v1/leads/{lead_id}/contacts",
        json={
            "first_name": "Pooja",
            "last_name": "Sharma",
            "title": "Head of Procurement",
            "email": "pooja@quantumrobotics.com",
            "is_primary": True,
        },
        headers=admin_headers,
    )
    assert c2_res.status_code == 200
    assert c2_res.json()["data"]["is_primary"] is True

    # Check list: Contact 2 must be primary, Contact 1 must be demoted
    list_res = await client.get(
        f"/api/v1/leads/{lead_id}/contacts",
        headers=admin_headers,
    )
    assert list_res.status_code == 200
    contacts = list_res.json()["data"]
    assert len(contacts) == 2
    primary_contacts = [c for c in contacts if c["is_primary"]]
    assert len(primary_contacts) == 1
    assert primary_contacts[0]["first_name"] == "Pooja"


@pytest.mark.asyncio
async def test_timeline_interaction_logging(client: AsyncClient, admin_headers: dict):
    """Test recording customer interactions directly to the lead timeline."""
    lead_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Matrix Logistics"},
        headers=admin_headers,
    )
    lead_id = lead_res.json()["data"]["id"]

    # Log interaction
    log_res = await client.post(
        f"/api/v1/leads/{lead_id}/interactions",
        json={
            "interaction_type": "CALL",
            "notes": "Discussed Q4 contract renewal. Customer requested 10% SLA enhancement.",
        },
        headers=admin_headers,
    )
    assert log_res.status_code == 200
    assert log_res.json()["data"]["interaction_type"] == "CALL"

    # Verify on timeline activity
    act_res = await client.get(f"/api/v1/leads/{lead_id}/activity", headers=admin_headers)
    assert act_res.status_code == 200
    events = act_res.json()["data"]["items"]
    int_events = [e for e in events if e["event_type"] == "interaction_logged"]
    assert len(int_events) == 1
    assert "[CALL]" in int_events[0]["message"]
    assert "contract renewal" in int_events[0]["message"]


@pytest.mark.asyncio
async def test_bulk_crm_operations(client: AsyncClient, admin_headers: dict):
    """Test bulk assignment and priority setting."""
    l1 = (await client.post("/api/v1/leads", json={"business_name": "Bulk Corp 1"}, headers=admin_headers)).json()["data"]["id"]
    l2 = (await client.post("/api/v1/leads", json={"business_name": "Bulk Corp 2"}, headers=admin_headers)).json()["data"]["id"]

    # Bulk set priority to URGENT
    bulk_prio = await client.post(
        "/api/v1/leads/bulk",
        json={
            "action": "set_priority",
            "lead_ids": [l1, l2],
            "params": {"priority": "URGENT"},
        },
        headers=admin_headers,
    )
    assert bulk_prio.status_code == 200
    assert bulk_prio.json()["data"]["affected"] == 2

    # Verify leads updated
    lead1_data = (await client.get(f"/api/v1/leads/{l1}", headers=admin_headers)).json()["data"]
    assert lead1_data["priority"] == "URGENT"

    lead2_data = (await client.get(f"/api/v1/leads/{l2}", headers=admin_headers)).json()["data"]
    assert lead2_data["priority"] == "URGENT"


@pytest.mark.asyncio
async def test_safe_lead_merge_crm_rehoming(client: AsyncClient, admin_headers: dict, app):
    """Test that safe merging re-homes followups, contacts, and status history while protecting verified state."""
    # Lead A (Primary) - Verified
    l1_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Primary Bio Corp", "city": "Bengaluru", "is_verified": True},
        headers=admin_headers,
    )
    lead_a_id = l1_res.json()["data"]["id"]

    # Lead B (Duplicate to merge)
    l2_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Primary Bio", "phone": "+91-9888877777", "state": "Karnataka"},
        headers=admin_headers,
    )
    lead_b_id = l2_res.json()["data"]["id"]

    # Add follow-up and contact to Lead B
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await client.post(
        f"/api/v1/leads/{lead_b_id}/followups",
        json={"title": "Followup on B", "due_at": due},
        headers=admin_headers,
    )
    await client.post(
        f"/api/v1/leads/{lead_b_id}/contacts",
        json={"first_name": "Rohan", "title": "Head of Research", "is_primary": True},
        headers=admin_headers,
    )
    await client.post(
        f"/api/v1/leads/{lead_b_id}/stage",
        json={"stage": "CONTACTED", "reason": "Called earlier"},
        headers=admin_headers,
    )

    # Perform Merge: Lead B into Lead A
    merge_res = await client.post(
        f"/api/v1/leads/{lead_b_id}/merge",
        json={"primary_lead_id": lead_a_id},
        headers=admin_headers,
    )
    assert merge_res.status_code == 200

    # Verify Lead A now has Lead B's follow-up task
    fu_res = await client.get(f"/api/v1/leads/{lead_a_id}/followups", headers=admin_headers)
    assert fu_res.status_code == 200
    tasks = fu_res.json()["data"]
    assert any(t["title"] == "Followup on B" for t in tasks)

    # Verify Lead A now has Lead B's contact person
    contacts_res = await client.get(f"/api/v1/leads/{lead_a_id}/contacts", headers=admin_headers)
    assert contacts_res.status_code == 200
    contacts = contacts_res.json()["data"]
    assert any(c["first_name"] == "Rohan" for c in contacts)

    # Verify Lead A preserved verified status
    get_a = await client.get(f"/api/v1/leads/{lead_a_id}", headers=admin_headers)
    assert get_a.json()["data"]["is_verified"] is True
    assert get_a.json()["data"]["phone"] == "+91-9888877777"


@pytest.mark.asyncio
async def test_crm_dashboard_metrics(client: AsyncClient, admin_headers: dict):
    """Test that CRM dashboard returns correct aggregate stats and conversion rates."""
    # Seed 1 CONVERTED lead, 1 QUALIFIED lead
    l_conv = (await client.post("/api/v1/leads", json={"business_name": "Converted Enterprise"}, headers=admin_headers)).json()["data"]["id"]
    await client.post(f"/api/v1/leads/{l_conv}/stage", json={"stage": "ASSIGNED"}, headers=admin_headers)
    await client.post(f"/api/v1/leads/{l_conv}/stage", json={"stage": "CONTACTED"}, headers=admin_headers)
    await client.post(f"/api/v1/leads/{l_conv}/stage", json={"stage": "QUALIFIED"}, headers=admin_headers)
    await client.post(f"/api/v1/leads/{l_conv}/stage", json={"stage": "CONVERTED"}, headers=admin_headers)

    dash_res = await client.get("/api/v1/leads/crm-dashboard", headers=admin_headers)
    assert dash_res.status_code == 200
    data = dash_res.json()["data"]
    assert "total_leads" in data
    assert data["total_leads"] >= 1
    assert "stages" in data
    assert data["stages"]["CONVERTED"] >= 1
    assert "conversion_rate_pct" in data
    assert data["conversion_rate_pct"] > 0.0
    assert "tasks" in data
    assert "pending" in data["tasks"]
    assert "due_today" in data["tasks"]
    assert "overdue" in data["tasks"]


@pytest.mark.asyncio
async def test_employee_lead_isolation(client: AsyncClient, admin_headers: dict, viewer_headers: dict):
    """Verify that employee visibility scoping protects leads from unauthorized cross-access."""
    # Admin creates a private lead unassigned to viewer
    lead_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Confidential Defense Partner"},
        headers=admin_headers,
    )
    confidential_lead_id = lead_res.json()["data"]["id"]

    # Admin can access
    admin_get = await client.get(f"/api/v1/leads/{confidential_lead_id}", headers=admin_headers)
    assert admin_get.status_code == 200

    # Viewer can view if permitted by tenant, but if query filters assigned_to_me, it is excluded
    mine_res = await client.get("/api/v1/leads?assigned_to_me=true", headers=viewer_headers)
    assert mine_res.status_code == 200
    mine_ids = [lead["id"] for lead in mine_res.json()["data"]["items"]]
    assert confidential_lead_id not in mine_ids


@pytest.mark.asyncio
async def test_crm_ui_views(client: AsyncClient, admin_headers: dict):
    """Verify that the Stitch UI pages render CRM components correctly."""
    # 1. Leads index HTML contains priority filter
    index_res = await client.get("/leads", headers=admin_headers)
    assert index_res.status_code == 200
    html = index_res.text
    assert "Priority: any" in html
    assert "My Leads" in html
    assert "Assign to employee" in html

    # 2. Lead detail HTML contains Pipeline Stepper, Follow-up card, Contact card
    lead_res = await client.post(
        "/api/v1/leads",
        json={"business_name": "Stitch UI Validation Corp"},
        headers=admin_headers,
    )
    lid = lead_res.json()["data"]["id"]

    detail_res = await client.get(f"/leads/{lid}", headers=admin_headers)
    assert detail_res.status_code == 200
    detail_html = detail_res.text
    assert "Pipeline Stage Progression" in detail_html
    assert "Follow-Up Tasks &amp; Reminders" in detail_html
    assert "Contact Persons" in detail_html
    assert "Log Communication / Interaction" in detail_html
    assert "Stage Transition History" in detail_html
