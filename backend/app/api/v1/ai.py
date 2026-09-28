"""Phase 9 — AI Agents, Lead Intelligence & Sales Copilot REST API.

All endpoints enforce RBAC via require_permission dependency. No AI mock responses in production.
All data returned is real persisted records — never fabricated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission
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

router = APIRouter(prefix="/ai", tags=["AI Agents"])


async def _ctx_for(session: AsyncSession, user: User):
    """Resolve tenancy context for the caller."""
    from app.services import authorization as authz
    from app.services import rbac as rbac_service

    perms = getattr(user, "_qbit_perms", None)
    if perms is None:
        perms = await rbac_service.load_user_permissions(session, user.id)
    return await authz.resolve_context(session, user, perms)


async def _visible_lead(session: AsyncSession, lead_id: uuid.UUID, user: User) -> Lead:
    """IDOR-safe lead fetch: organization + visibility scope, 404 on foreign."""
    from app.services import authorization as authz

    ctx = await _ctx_for(session, user)
    return await authz.get_visible_or_404(session, Lead, lead_id, ctx)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

class RunAgentRequest(BaseModel):
    lead_id: str = Field(..., description="Lead UUID to run the agent against")
    input_overrides: dict[str, Any] = Field(default_factory=dict)


class ScoreOverrideRequest(BaseModel):
    override_score: int = Field(..., ge=0, le=100)
    override_reason: str = Field(..., min_length=1, max_length=1000)


class ProposalReviewRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------

@router.get("/agents", summary="List available AI agents")
async def list_agents(
    request: Request,
    user: User = Depends(require_permission("ai.view")),
    session: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    from app.services.ai.agents import AVAILABLE_AGENTS

    return {
        "success": True,
        "data": {
            "agents": AVAILABLE_AGENTS,
            "provider": settings.AI_PROVIDER,
            "configured": bool(settings.AI_API_KEY) or settings.AI_PROVIDER == "mock",
        },
    }


@router.post("/agents/{slug}/run", summary="Queue an agent run for a lead")
async def queue_agent_run(
    slug: str,
    body: RunAgentRequest,
    request: Request,
    user: User = Depends(require_permission("ai.run_agents")),
    session: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings

    from app.services.ai.agents import AVAILABLE_AGENTS
    if slug not in AVAILABLE_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {slug}")

    # Validate lead exists and belongs to caller's org / visibility
    try:
        lid = uuid.UUID(body.lead_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid lead_id UUID")
    lead = await _visible_lead(session, lid, user)
    ctx = await _ctx_for(session, user)
    org_id = lead.organization_id or ctx.organization_id

    now = datetime.now(timezone.utc)
    run = AIAgentRun(
        organization_id=org_id,
        user_id=user.id,
        lead_id=lead.id,
        task_name=f"ai_agent:{slug}",
        status=AgentRunStatus.QUEUED.value,
        input_data={"lead_id": str(lead.id), **body.input_overrides},
        created_at=now,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    # Run synchronously in the same request
    try:
        from app.services.ai.gateway import get_gateway
        from app.services.ai.agents import get_agent
        gateway = get_gateway(settings)
        agent = get_agent(slug, gateway, settings)

        async with session.begin_nested():
            run.status = AgentRunStatus.RUNNING.value
            output = await agent.run(session, run)
            run.status = AgentRunStatus.COMPLETED.value
            run.output_data = output
            run.completed_at = datetime.now(timezone.utc)

        await session.commit()
    except Exception as exc:  # noqa: BLE001
        run.status = AgentRunStatus.FAILED.value
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()

    await session.refresh(run)
    return {"success": True, "data": run.to_dict()}


@router.get("/runs/{run_id}", summary="Get agent run details")
async def get_run(
    run_id: uuid.UUID,
    user: User = Depends(require_permission("ai.view")),
    session: AsyncSession = Depends(get_db),
):
    ctx = await _ctx_for(session, user)
    result = await session.execute(select(AIAgentRun).where(AIAgentRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if not ctx.is_super_admin and run.organization_id is not None and run.organization_id != ctx.organization_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return {"success": True, "data": run.to_dict()}


@router.post("/runs/{run_id}/cancel", summary="Cancel a queued agent run")
async def cancel_run(
    run_id: uuid.UUID,
    user: User = Depends(require_permission("ai.run_agents")),
    session: AsyncSession = Depends(get_db),
):
    ctx = await _ctx_for(session, user)
    result = await session.execute(select(AIAgentRun).where(AIAgentRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if not ctx.is_super_admin and run.organization_id is not None and run.organization_id != ctx.organization_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if run.status not in (AgentRunStatus.QUEUED.value, AgentRunStatus.RUNNING.value):
        raise HTTPException(status_code=409, detail="Run cannot be cancelled in its current state")
    run.status = AgentRunStatus.CANCELLED.value
    run.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"success": True, "data": {"id": str(run_id), "status": run.status}}


# ---------------------------------------------------------------------------
# Lead Intelligence
# ---------------------------------------------------------------------------

@router.post("/leads/{lead_id}/enrich", summary="Trigger lead enrichment")
async def enrich_lead(
    lead_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permission("leads.enrich")),
    session: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    from app.services.ai.gateway import get_gateway
    from app.services.ai.agents import get_agent

    lead = await _visible_lead(session, lead_id, user)
    ctx = await _ctx_for(session, user)
    org_id = lead.organization_id or ctx.organization_id

    now = datetime.now(timezone.utc)
    run = AIAgentRun(
        organization_id=org_id,
        user_id=user.id,
        lead_id=lead.id,
        task_name="ai_agent:lead-enrichment",
        status=AgentRunStatus.RUNNING.value,
        input_data={"lead_id": str(lead.id)},
        started_at=now,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    try:
        gateway = get_gateway(settings)
        agent = get_agent("lead-enrichment", gateway, settings)
        output = await agent.run(session, run)
        run.status = AgentRunStatus.COMPLETED.value
        run.output_data = output
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        run.status = AgentRunStatus.FAILED.value
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "data": run.to_dict()}


@router.get("/leads/{lead_id}/intelligence", summary="Get lead intelligence record")
async def get_lead_intelligence(
    lead_id: uuid.UUID,
    user: User = Depends(require_permission("leads.view_intelligence")),
    session: AsyncSession = Depends(get_db),
):
    await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(LeadIntelligence).where(LeadIntelligence.lead_id == lead_id)
    )
    intel = result.scalar_one_or_none()
    if intel is None:
        raise HTTPException(status_code=404, detail="No intelligence record for this lead")
    return {"success": True, "data": intel.to_dict()}


@router.get("/leads/{lead_id}/score", summary="Get latest lead score")
async def get_lead_score(
    lead_id: uuid.UUID,
    user: User = Depends(require_permission("leads.view_score")),
    session: AsyncSession = Depends(get_db),
):
    await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(LeadScoreRecord)
        .where(LeadScoreRecord.lead_id == lead_id)
        .order_by(desc(LeadScoreRecord.created_at))
        .limit(1)
    )
    score = result.scalar_one_or_none()
    if score is None:
        raise HTTPException(status_code=404, detail="No score record for this lead")
    return {"success": True, "data": score.to_dict()}


@router.post("/leads/{lead_id}/score", summary="Calculate/recalculate lead score")
async def score_lead(
    lead_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permission("leads.enrich")),
    session: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    from app.services.ai.gateway import get_gateway
    from app.services.ai.agents import get_agent

    lead = await _visible_lead(session, lead_id, user)
    ctx = await _ctx_for(session, user)
    org_id = lead.organization_id or ctx.organization_id

    now = datetime.now(timezone.utc)
    run = AIAgentRun(
        organization_id=org_id,
        user_id=user.id,
        lead_id=lead.id,
        task_name="ai_agent:lead-scoring",
        status=AgentRunStatus.RUNNING.value,
        input_data={"lead_id": str(lead.id)},
        started_at=now,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    try:
        gateway = get_gateway(settings)
        agent = get_agent("lead-scoring", gateway, settings)
        output = await agent.run(session, run)
        run.status = AgentRunStatus.COMPLETED.value
        run.output_data = output
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        run.status = AgentRunStatus.FAILED.value
        run.error_message = str(exc)
        await session.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "data": run.to_dict()}


@router.post("/leads/{lead_id}/score/override", summary="Manually override lead score")
async def override_lead_score(
    lead_id: uuid.UUID,
    body: ScoreOverrideRequest,
    user: User = Depends(require_permission("ai.manage_agents")),
    session: AsyncSession = Depends(get_db),
):
    await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(LeadScoreRecord)
        .where(LeadScoreRecord.lead_id == lead_id)
        .order_by(desc(LeadScoreRecord.created_at))
        .limit(1)
    )
    score = result.scalar_one_or_none()
    if score is None:
        raise HTTPException(status_code=404, detail="No score record for this lead")
    score.is_overridden = True
    score.override_score = body.override_score
    score.override_reason = body.override_reason
    score.overridden_by = user.id
    await session.commit()
    return {"success": True, "data": score.to_dict()}


@router.get("/leads/{lead_id}/sales-brief", summary="Get latest sales brief for a lead")
async def get_sales_brief(
    lead_id: uuid.UUID,
    user: User = Depends(require_permission("leads.view_intelligence")),
    session: AsyncSession = Depends(get_db),
):
    await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(SalesBrief)
        .where(SalesBrief.lead_id == lead_id)
        .order_by(desc(SalesBrief.created_at))
        .limit(1)
    )
    brief = result.scalar_one_or_none()
    if brief is None:
        raise HTTPException(status_code=404, detail="No sales brief for this lead")
    return {"success": True, "data": brief.to_dict()}


@router.post("/leads/{lead_id}/sales-brief/generate", summary="Generate a new sales brief")
async def generate_sales_brief(
    lead_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permission("leads.enrich")),
    session: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    from app.services.ai.gateway import get_gateway
    from app.services.ai.agents import get_agent

    lead = await _visible_lead(session, lead_id, user)
    ctx = await _ctx_for(session, user)
    org_id = lead.organization_id or ctx.organization_id

    now = datetime.now(timezone.utc)
    run = AIAgentRun(
        organization_id=org_id,
        user_id=user.id,
        lead_id=lead.id,
        task_name="ai_agent:sales-intelligence",
        status=AgentRunStatus.RUNNING.value,
        input_data={"lead_id": str(lead.id)},
        started_at=now,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    try:
        gateway = get_gateway(settings)
        agent = get_agent("sales-intelligence", gateway, settings)
        output = await agent.run(session, run)
        run.status = AgentRunStatus.COMPLETED.value
        run.output_data = output
        run.completed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        run.status = AgentRunStatus.FAILED.value
        run.error_message = str(exc)
        await session.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "data": run.to_dict()}


# ---------------------------------------------------------------------------
# Enrichment Proposals
# ---------------------------------------------------------------------------

@router.get("/leads/{lead_id}/enrichment-proposals", summary="List enrichment proposals")
async def list_enrichment_proposals(
    lead_id: uuid.UUID,
    user: User = Depends(require_permission("leads.view_intelligence")),
    session: AsyncSession = Depends(get_db),
):
    await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(LeadEnrichmentProposal)
        .where(LeadEnrichmentProposal.lead_id == lead_id)
        .order_by(desc(LeadEnrichmentProposal.created_at))
    )
    proposals = result.scalars().all()
    return {"success": True, "data": [p.to_dict() for p in proposals]}


@router.post("/leads/{lead_id}/enrichment-proposals/{proposal_id}/review",
             summary="Approve or reject an enrichment proposal")
async def review_enrichment_proposal(
    lead_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: ProposalReviewRequest,
    user: User = Depends(require_permission("leads.enrich")),
    session: AsyncSession = Depends(get_db),
):
    lead = await _visible_lead(session, lead_id, user)
    result = await session.execute(
        select(LeadEnrichmentProposal)
        .where(LeadEnrichmentProposal.id == proposal_id)
        .where(LeadEnrichmentProposal.lead_id == lead.id)
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != ProposalStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Proposal already reviewed")

    now = datetime.now(timezone.utc)
    proposal.reviewed_by = user.id
    proposal.review_notes = body.notes
    proposal.reviewed_at = now

    if body.action == "approve":
        proposal.status = ProposalStatus.APPROVED.value
        # Apply changes to the lead
        changes = proposal.proposed_changes or {}
        SAFE_FIELDS = {
            "phone", "email", "website", "address", "city", "state",
            "country", "category", "description",
        }
        for field, value in changes.items():
            if field in SAFE_FIELDS and hasattr(lead, field):
                setattr(lead, field, value)
        proposal.status = ProposalStatus.APPLIED.value
    else:
        proposal.status = ProposalStatus.REJECTED.value

    await session.commit()
    return {"success": True, "data": proposal.to_dict()}


# ---------------------------------------------------------------------------
# AI Usage Dashboard
# ---------------------------------------------------------------------------

@router.get("/usage", summary="AI token usage and cost summary")
async def get_ai_usage(
    user: User = Depends(require_permission("ai.view_usage")),
    session: AsyncSession = Depends(get_db),
):
    ctx = await _ctx_for(session, user)
    q = select(AIUsageRecord).order_by(desc(AIUsageRecord.created_at)).limit(100)
    if not ctx.is_super_admin:
        q = q.where(AIUsageRecord.organization_id == ctx.organization_id)
    result = await session.execute(q)
    records = result.scalars().all()

    total_tokens = sum(r.total_tokens for r in records)
    total_cost = sum(r.estimated_cost for r in records)

    return {
        "success": True,
        "data": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "record_count": len(records),
            "records": [r.to_dict() for r in records],
        },
    }




# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@router.get("/products", summary="List QBIT POS product catalog")
async def list_products(
    user: User = Depends(require_permission("products.view")),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.category, Product.name)
    )
    products = result.scalars().all()
    return {"success": True, "data": [p.to_dict() for p in products]}
