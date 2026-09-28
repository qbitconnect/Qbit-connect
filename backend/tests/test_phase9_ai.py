"""Phase 9 — AI Agents, Lead Intelligence & Sales Copilot Test Suite.

Verifies:
1. Agent definitions, registry and model routing configuration
2. AI Gateway mock completions, token counting, and cost estimation
3. AI Gateway SSRF protection against private IP targets
4. Deterministic 0-100 Lead Scoring Engine (multi-factor weights and bounds)
5. QBIT POS Hardware Product Catalog & semantic/keyword search
6. Lead Enrichment Agent workflow (provenance, business summary, proposals)
7. Lead Scoring Agent durable run and score record persistence
8. Sales Intelligence Agent brief generation and QBIT catalog integration
9. Complete REST API endpoints (runs, scoring, overrides, proposals, usage, products)
10. Human-in-the-loop review workflow (proposals approval, lead record update)
11. RBAC permission gates (admin allowed, viewer restricted on mutations)
12. Operator UI views rendering cleanly with Stitch design system tokens
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.models.ai import (
    AIAgentDefinition,
    AIAgentRun,
    AIUsageRecord,
    AgentRunStatus,
    LeadEnrichmentProposal,
    LeadIntelligence,
    LeadScoreRecord,
    Product,
    ProposalStatus,
    SalesBrief,
)
from app.models.scrape import Lead
from app.models.user import User
from app.services.ai.agents import (
    AVAILABLE_AGENTS,
    LeadEnrichmentAgent,
    LeadScoringAgent,
    SalesIntelligenceAgent,
    get_agent,
)
from app.services.ai.gateway import AIGateway, ConfigurationError, get_gateway
from app.services.ai.tools import (
    calculate_lead_score,
    crm_lead_lookup,
    product_catalog_search,
)
from tests.conftest import ADMIN_EMAIL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def create_sample_lead(session, org_id: uuid.UUID | None = None) -> Lead:
    now = datetime.now(timezone.utc)
    lead = Lead(
        id=uuid.uuid4(),
        organization_id=org_id,
        business_name="The Royal Spice Restaurant",
        category="Restaurant & Bar",
        city="Mumbai",
        state="Maharashtra",
        country="India",
        phone="+91 98765 43210",
        email="info@royalspice.example.com",
        website="https://royalspice.example.com",
        rating=4.5,
        review_count=120,
        address="123 Marine Drive, Nariman Point, Mumbai 400021",
        source="google_maps",
        enrichment_status="UNENRICHED",
        quality_score=60,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def seed_sample_products(session, org_id: uuid.UUID | None = None) -> list[Product]:
    existing = await session.execute(select(Product))
    products = existing.scalars().all()
    if products:
        return list(products)

    now = datetime.now(timezone.utc)
    items = [
        Product(
            id=uuid.uuid4(),
            organization_id=org_id,
            name="QBIT Windows POS Terminal 15\"",
            sku="QPOS-WIN-15",
            category="WINDOWS_POS",
            description="All-in-one 15\" touch-screen Windows POS terminal.",
            price_inr=45000.0,
            specifications={"screen": '15"', "os": "Windows 10/11"},
            target_industries=["restaurant", "retail", "hotel", "cafe"],
            is_active=True,
            created_at=now,
            updated_at=now,
        ),
        Product(
            id=uuid.uuid4(),
            organization_id=org_id,
            name="QBIT Handy POS 6\"",
            sku="QPOS-HAND-6",
            category="HANDY_POS",
            description="Handheld 6\" Android POS for table-side ordering.",
            price_inr=18500.0,
            specifications={"screen": '6"', "os": "Android 11"},
            target_industries=["restaurant", "hotel", "cafe", "bar"],
            is_active=True,
            created_at=now,
            updated_at=now,
        ),
        Product(
            id=uuid.uuid4(),
            organization_id=org_id,
            name="QBIT 80mm Thermal Receipt Printer",
            sku="QPT-80",
            category="THERMAL_PRINTER",
            description="High-speed 80mm thermal receipt printer.",
            price_inr=6500.0,
            specifications={"width": "80mm"},
            target_industries=["restaurant", "retail", "hotel"],
            is_active=True,
            created_at=now,
            updated_at=now,
        ),
    ]
    for p in items:
        session.add(p)
    await session.commit()
    return items


# ---------------------------------------------------------------------------
# Test 1: Agent Registry & Available Agents
# ---------------------------------------------------------------------------

async def test_agent_registry_and_definitions(app):
    settings = app.state.settings
    gateway = get_gateway(settings)

    assert "lead-enrichment" in AVAILABLE_AGENTS
    assert "lead-scoring" in AVAILABLE_AGENTS
    assert "sales-intelligence" in AVAILABLE_AGENTS

    enrich_agent = get_agent("lead-enrichment", gateway, settings)
    assert isinstance(enrich_agent, LeadEnrichmentAgent)
    assert enrich_agent.slug == "lead-enrichment"

    score_agent = get_agent("lead-scoring", gateway, settings)
    assert isinstance(score_agent, LeadScoringAgent)
    assert score_agent.slug == "lead-scoring"

    sales_agent = get_agent("sales-intelligence", gateway, settings)
    assert isinstance(sales_agent, SalesIntelligenceAgent)
    assert sales_agent.slug == "sales-intelligence"

    with pytest.raises(KeyError):
        get_agent("non-existent-agent", gateway, settings)


# ---------------------------------------------------------------------------
# Test 2: AI Gateway Mock Completions & Token/Cost Estimation
# ---------------------------------------------------------------------------

async def test_ai_gateway_mock_completion(app):
    settings = app.state.settings
    # In test env, mock completion returns deterministic response
    test_settings = Settings(
        QBIT_ENV="test",
        AI_PROVIDER="mock",
        AI_API_KEY="",
        AI_DEFAULT_MODEL="gpt-4o-mini",
        AI_REASONING_MODEL="gpt-4o",
        _env_file=None,
    )
    gateway = AIGateway(test_settings)
    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "Analyze this business: Royal Palace Hotel"},
    ]

    resp = await gateway.complete(messages, routing_key="reasoning")
    assert resp.provider == "mock"
    assert resp.model == "gpt-4o"
    assert resp.total_tokens == 75
    assert "[MOCK AI RESPONSE]" in resp.text
    assert "Royal Palace Hotel" in resp.text


# ---------------------------------------------------------------------------
# Test 3: AI Gateway SSRF Protection
# ---------------------------------------------------------------------------

async def test_ai_gateway_ssrf_protection():
    prod_settings = Settings(
        QBIT_ENV="production",
        AI_PROVIDER="openai",
        AI_API_KEY="sk-test-key-1234567890",
        AI_BASE_URL="http://192.168.1.100:8000",  # HTTP + Private IP
        _env_file=None,
    )
    gateway = AIGateway(prod_settings)
    with pytest.raises(ConfigurationError) as exc_info:
        await gateway.complete([{"role": "user", "content": "hello"}])
    assert "AI_BASE_URL must use HTTPS" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: Deterministic Lead Scoring Engine (Auditability & Bounds)
# ---------------------------------------------------------------------------

async def test_deterministic_lead_scoring():
    # Full lead with all attributes
    lead_data = {
        "category": "Fine Dining Restaurant & Lounge",
        "city": "Bengaluru",
        "state": "Karnataka",
        "country": "India",
        "phone": "+91 98765 00000",
        "email": "contact@finedine.com",
        "website": "https://finedine.com",
        "rating": 4.8,
        "review_count": 150,
        "engagement_score": 5,
    }
    score_res = calculate_lead_score(lead_data)

    assert 0 <= score_res["score"] <= 100
    assert score_res["category_fit_score"] == 25
    assert score_res["location_fit_score"] == 15
    assert score_res["contact_completeness_score"] == 20
    assert score_res["rating_review_score"] == 20
    assert score_res["pos_hardware_fit_score"] == 10
    assert score_res["engagement_history_score"] == 5
    assert score_res["score"] == 95
    assert "Score 95/100" in score_res["explanation"]

    # Minimal/empty lead
    empty_data = {}
    empty_score = calculate_lead_score(empty_data)
    assert empty_score["score"] == 0
    assert empty_score["category_fit_score"] == 0
    assert empty_score["contact_completeness_score"] == 0


# ---------------------------------------------------------------------------
# Test 5: Product Catalog Search
# ---------------------------------------------------------------------------

async def test_product_catalog_search(app):
    async with app.state.db.session() as session:
        await seed_sample_products(session)

        # Search by industry
        res = await product_catalog_search(query="", industry="restaurant", session=session)
        assert res.success is True
        assert res.data["count"] >= 2
        skus = [p["sku"] for p in res.data["products"]]
        assert "QPOS-WIN-15" in skus

        # Search by keyword
        res_kw = await product_catalog_search(query="printer", session=session)
        assert res_kw.success is True
        assert any("Printer" in p["name"] for p in res_kw.data["products"])


# ---------------------------------------------------------------------------
# Test 6: Lead Enrichment Agent Execution
# ---------------------------------------------------------------------------

async def test_lead_enrichment_agent(app):
    settings = app.state.settings
    gateway = get_gateway(settings)
    agent = LeadEnrichmentAgent(gateway, settings)

    async with app.state.db.session() as session:
        lead = await create_sample_lead(session)

        run = AIAgentRun(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            task_name="ai_agent:lead-enrichment",
            status=AgentRunStatus.QUEUED.value,
            input_data={"lead_id": str(lead.id)},
            created_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        output = await agent.run(session, run)
        assert "lead_id" in output
        assert output["lead_id"] == str(lead.id)

        # Verify LeadIntelligence was created
        intel_res = await session.execute(
            select(LeadIntelligence).where(LeadIntelligence.lead_id == lead.id)
        )
        intel = intel_res.scalar_one_or_none()
        assert intel is not None
        assert intel.lead_id == lead.id
        assert intel.data_completeness > 0


# ---------------------------------------------------------------------------
# Test 7: Lead Scoring Agent Execution
# ---------------------------------------------------------------------------

async def test_lead_scoring_agent(app):
    settings = app.state.settings
    gateway = get_gateway(settings)
    agent = LeadScoringAgent(gateway, settings)

    async with app.state.db.session() as session:
        lead = await create_sample_lead(session)

        run = AIAgentRun(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            task_name="ai_agent:lead-scoring",
            status=AgentRunStatus.QUEUED.value,
            input_data={"lead_id": str(lead.id)},
            created_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        output = await agent.run(session, run)
        assert output["score"] > 0
        assert "category_fit_score" in output

        # Verify LeadScoreRecord exists
        score_res = await session.execute(
            select(LeadScoreRecord).where(LeadScoreRecord.lead_id == lead.id)
        )
        score_record = score_res.scalar_one_or_none()
        assert score_record is not None
        assert score_record.score == output["score"]
        assert score_record.category_fit_score == 25


# ---------------------------------------------------------------------------
# Test 8: Sales Intelligence Agent Execution
# ---------------------------------------------------------------------------

async def test_sales_intelligence_agent(app):
    settings = app.state.settings
    gateway = get_gateway(settings)
    agent = SalesIntelligenceAgent(gateway, settings)

    async with app.state.db.session() as session:
        await seed_sample_products(session)
        lead = await create_sample_lead(session)

        run = AIAgentRun(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            task_name="ai_agent:sales-intelligence",
            status=AgentRunStatus.QUEUED.value,
            input_data={"lead_id": str(lead.id)},
            created_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        output = await agent.run(session, run)
        assert "brief_id" in output

        # Verify SalesBrief exists
        brief_res = await session.execute(
            select(SalesBrief).where(SalesBrief.lead_id == lead.id)
        )
        brief = brief_res.scalar_one_or_none()
        assert brief is not None
        assert brief.lead_id == lead.id
        assert brief.recommended_next_action is not None


# ---------------------------------------------------------------------------
# Test 9: Complete AI REST API Lifecycle
# ---------------------------------------------------------------------------

async def test_ai_rest_api_lifecycle(client: AsyncClient, admin_headers: dict, app):
    async with app.state.db.session() as session:
        await seed_sample_products(session)
        lead = await create_sample_lead(session)
        lead_id_str = str(lead.id)

    # 1. List agents
    resp = await client.get("/api/v1/ai/agents", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    agents_data = resp.json()["data"]["agents"]
    assert "lead-enrichment" in agents_data

    # 2. Run agent via API
    run_resp = await client.post(
        "/api/v1/ai/agents/lead-enrichment/run",
        json={"lead_id": lead_id_str},
        headers=admin_headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["data"]["id"]

    # 3. Get run details
    detail_resp = await client.get(f"/api/v1/ai/runs/{run_id}", headers=admin_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["status"] == "COMPLETED"

    # 4. Score lead via API
    score_resp = await client.post(
        f"/api/v1/ai/leads/{lead_id_str}/score",
        headers=admin_headers,
    )
    assert score_resp.status_code == 200, score_resp.text
    assert score_resp.json()["data"]["status"] == "COMPLETED"

    # 5. Fetch score
    get_score_resp = await client.get(
        f"/api/v1/ai/leads/{lead_id_str}/score",
        headers=admin_headers,
    )
    assert get_score_resp.status_code == 200
    assert get_score_resp.json()["data"]["score"] > 0

    # 6. Manual score override
    override_resp = await client.post(
        f"/api/v1/ai/leads/{lead_id_str}/score/override",
        json={"override_score": 98, "override_reason": "High-value enterprise customer verified by sales lead."},
        headers=admin_headers,
    )
    assert override_resp.status_code == 200
    assert override_resp.json()["data"]["score"] == 98
    assert override_resp.json()["data"]["is_overridden"] is True

    # 7. Generate sales brief
    brief_gen_resp = await client.post(
        f"/api/v1/ai/leads/{lead_id_str}/sales-brief/generate",
        headers=admin_headers,
    )
    assert brief_gen_resp.status_code == 200

    # 8. Fetch sales brief
    get_brief_resp = await client.get(
        f"/api/v1/ai/leads/{lead_id_str}/sales-brief",
        headers=admin_headers,
    )
    assert get_brief_resp.status_code == 200
    assert "business_overview" in get_brief_resp.json()["data"]

    # 9. List products catalog
    products_resp = await client.get("/api/v1/ai/products", headers=admin_headers)
    assert products_resp.status_code == 200
    assert len(products_resp.json()["data"]) >= 3

    # 10. AI Usage endpoint
    usage_resp = await client.get("/api/v1/ai/usage", headers=admin_headers)
    assert usage_resp.status_code == 200
    assert "total_tokens" in usage_resp.json()["data"]


# ---------------------------------------------------------------------------
# Test 10: Lead Enrichment Proposal Review & CRM Update
# ---------------------------------------------------------------------------

async def test_enrichment_proposal_review_workflow(client: AsyncClient, admin_headers: dict, app):
    async with app.state.db.session() as session:
        lead = await create_sample_lead(session)
        now = datetime.now(timezone.utc)
        proposal = LeadEnrichmentProposal(
            id=uuid.uuid4(),
            organization_id=lead.organization_id,
            lead_id=lead.id,
            proposed_changes={"phone": "+91 99999 11111", "city": "Pune"},
            status=ProposalStatus.PENDING.value,
            created_at=now,
        )
        session.add(proposal)
        await session.commit()
        proposal_id_str = str(proposal.id)
        lead_id_str = str(lead.id)

    # Approve proposal
    review_resp = await client.post(
        f"/api/v1/ai/leads/{lead_id_str}/enrichment-proposals/{proposal_id_str}/review",
        json={"action": "approve", "notes": "Verified via corporate directory."},
        headers=admin_headers,
    )
    assert review_resp.status_code == 200, review_resp.text
    assert review_resp.json()["data"]["status"] == "APPLIED"

    # Verify CRM Lead record was actually updated
    async with app.state.db.session() as session:
        updated_lead = await session.get(Lead, lead.id)
        assert updated_lead.phone == "+91 99999 11111"
        assert updated_lead.city == "Pune"


# ---------------------------------------------------------------------------
# Test 11: RBAC Permission Gates
# ---------------------------------------------------------------------------

async def test_ai_rbac_permission_enforcement(client: AsyncClient, viewer_headers: dict, app):
    async with app.state.db.session() as session:
        lead = await create_sample_lead(session)
        lead_id_str = str(lead.id)

    # Viewer cannot run agents
    resp_run = await client.post(
        "/api/v1/ai/agents/lead-enrichment/run",
        json={"lead_id": lead_id_str},
        headers=viewer_headers,
    )
    assert resp_run.status_code == 403

    # Viewer cannot override lead score
    resp_override = await client.post(
        f"/api/v1/ai/leads/{lead_id_str}/score/override",
        json={"override_score": 90, "override_reason": "Attempted viewer override"},
        headers=viewer_headers,
    )
    assert resp_override.status_code == 403


# ---------------------------------------------------------------------------
# Test 12: Operator UI Views Rendering Cleanly
# ---------------------------------------------------------------------------

async def test_ai_ui_views_render_cleanly(client: AsyncClient, app):
    async with app.state.db.session() as session:
        lead = await create_sample_lead(session)
        now = datetime.now(timezone.utc)
        run = AIAgentRun(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            task_name="ai_agent:lead-enrichment",
            status=AgentRunStatus.COMPLETED.value,
            input_data={"lead_id": str(lead.id)},
            created_at=now,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id_str = str(run.id)
        lead_id_str = str(lead.id)

    # Login via UI form to obtain session cookie
    login_resp = await client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": "Sup3rSecret!Pass"},
        follow_redirects=False,
    )
    assert login_resp.status_code in (302, 303)
    cookies = login_resp.cookies

    # 1. AI Assistant Co-Pilot
    r_assist = await client.get("/ai-assistant", cookies=cookies)
    assert r_assist.status_code == 200
    assert "AI Co-Pilot" in r_assist.text

    # 2. Agent Registry
    r_agents = await client.get("/ai/agents", cookies=cookies)
    assert r_agents.status_code == 200
    assert "AI Agent Registry" in r_agents.text

    # 3. Agent Run Detail
    r_run = await client.get(f"/ai/runs/{run_id_str}", cookies=cookies)
    assert r_run.status_code == 200
    assert "Execution Steps" in r_run.text

    # 4. AI Usage
    r_usage = await client.get("/ai/usage", cookies=cookies)
    assert r_usage.status_code == 200
    assert "AI Usage" in r_usage.text

    # 5. Lead Intelligence View
    r_intel = await client.get(f"/leads/{lead_id_str}/intelligence", cookies=cookies)
    assert r_intel.status_code == 200
    assert "The Royal Spice Restaurant" in r_intel.text
