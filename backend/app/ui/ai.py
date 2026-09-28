"""Operator UI — AI Assistant, Agent Registry & Lead Intelligence (Phase 9).

Pages:
    GET  /ai-assistant          AI Co-Pilot chat workspace
    GET  /ai/agents             Agent registry + builder
    GET  /ai/runs/{id}          Agent run detail with step timeline
    GET  /ai/usage              AI token usage and cost dashboard
    GET  /leads/{id}/intelligence    Lead intelligence enrichment view
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.logging import get_logger
from app.models.ai import (
    AIAgentRun,
    AIUsageRecord,
    AgentRunStatus,
    LeadEnrichmentProposal,
    LeadIntelligence,
    LeadScoreRecord,
    Product,
    SalesBrief,
)
from app.models.scrape import Lead
from app.models.user import User
from app.ui import _ctx, templates, ui_user_for

logger = get_logger("qbit.ui.ai")

ai_view = ui_user_for("ai.view")
ai_usage_view = ui_user_for("ai.view_usage")
leads_intel_view = ui_user_for("leads.view_intelligence")

router = APIRouter(tags=["ai-ui"])


# --- 1. AI Assistant Co-Pilot -------------------------------------------------

@router.get("/ai-assistant", response_class=HTMLResponse)
async def ai_assistant(
    request: Request,
    user: Annotated[User, Depends(ai_view)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """AI Co-Pilot workspace — chat interface with tool panel."""
    settings = request.app.state.settings

    # Recent agent runs for the activity panel
    result = await session.execute(
        select(AIAgentRun)
        .order_by(desc(AIAgentRun.created_at))
        .limit(10)
    )
    recent_runs = result.scalars().all()

    # Available agents info
    from app.services.ai.agents import AVAILABLE_AGENTS
    configured = bool(settings.AI_API_KEY) or settings.AI_PROVIDER == "mock" or settings.QBIT_ENV == "test"

    return templates.TemplateResponse(
        request,
        "ai/assistant.html",
        _ctx(
            request, user,
            recent_runs=recent_runs,
            available_agents=AVAILABLE_AGENTS,
            ai_provider=settings.AI_PROVIDER,
            ai_configured=configured,
            page_title="AI Assistant",
        ),
    )


# --- 2. Agent Registry --------------------------------------------------------

@router.get("/ai/agents", response_class=HTMLResponse)
async def agent_registry(
    request: Request,
    user: Annotated[User, Depends(ai_view)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Agent registry — list and manage AI agents."""
    settings = request.app.state.settings
    from app.services.ai.agents import AVAILABLE_AGENTS, _AGENT_CLASSES

    agent_meta = [
        {
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "routing_key": cls.routing_key,
        }
        for slug, cls in _AGENT_CLASSES.items()
    ]

    configured = bool(settings.AI_API_KEY) or settings.AI_PROVIDER == "mock" or settings.QBIT_ENV == "test"

    return templates.TemplateResponse(
        request,
        "ai/agents.html",
        _ctx(
            request, user,
            agents=agent_meta,
            ai_provider=settings.AI_PROVIDER,
            ai_configured=configured,
            page_title="AI Agents",
        ),
    )


# --- 3. Agent Run Detail ------------------------------------------------------

@router.get("/ai/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    run_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(ai_view)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Agent run detail — step-by-step execution timeline."""
    result = await session.execute(
        select(AIAgentRun).where(AIAgentRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return RedirectResponse(url="/ai/agents", status_code=303)

    return templates.TemplateResponse(
        request,
        "ai/run_detail.html",
        _ctx(
            request, user,
            run=run,
            steps=run.steps or [],
            page_title=f"Run — {run.task_name}",
        ),
    )


# --- 4. AI Usage Dashboard ----------------------------------------------------

@router.get("/ai/usage", response_class=HTMLResponse)
async def ai_usage(
    request: Request,
    user: Annotated[User, Depends(ai_usage_view)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """AI token usage and cost monitoring dashboard."""
    result = await session.execute(
        select(AIUsageRecord)
        .order_by(desc(AIUsageRecord.created_at))
        .limit(200)
    )
    records = result.scalars().all()

    total_tokens = sum(r.total_tokens for r in records)
    total_cost = round(sum(r.estimated_cost for r in records), 6)

    # Group by provider/model
    by_model: dict[str, dict] = {}
    for r in records:
        key = f"{r.provider}/{r.model}"
        if key not in by_model:
            by_model[key] = {"provider": r.provider, "model": r.model, "tokens": 0, "cost": 0.0, "calls": 0}
        by_model[key]["tokens"] += r.total_tokens
        by_model[key]["cost"] += r.estimated_cost
        by_model[key]["calls"] += 1

    return templates.TemplateResponse(
        request,
        "ai/usage.html",
        _ctx(
            request, user,
            records=records[:50],
            by_model=list(by_model.values()),
            total_tokens=total_tokens,
            total_cost=total_cost,
            page_title="AI Usage & Cost",
        ),
    )


# --- 5. Lead Intelligence View ------------------------------------------------

@router.get("/leads/{lead_id}/intelligence", response_class=HTMLResponse)
async def lead_intelligence(
    lead_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(leads_intel_view)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Lead intelligence drawer — enrichment, score, decision makers, sales brief."""
    # Lead
    lead_result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if lead is None:
        return RedirectResponse(url="/leads", status_code=303)

    # Intelligence
    intel_result = await session.execute(
        select(LeadIntelligence).where(LeadIntelligence.lead_id == lead_id)
    )
    intelligence = intel_result.scalar_one_or_none()

    # Latest score
    score_result = await session.execute(
        select(LeadScoreRecord)
        .where(LeadScoreRecord.lead_id == lead_id)
        .order_by(desc(LeadScoreRecord.created_at))
        .limit(1)
    )
    score = score_result.scalar_one_or_none()

    # Sales brief
    brief_result = await session.execute(
        select(SalesBrief)
        .where(SalesBrief.lead_id == lead_id)
        .order_by(desc(SalesBrief.created_at))
        .limit(1)
    )
    sales_brief = brief_result.scalar_one_or_none()

    # Pending proposals
    proposals_result = await session.execute(
        select(LeadEnrichmentProposal)
        .where(LeadEnrichmentProposal.lead_id == lead_id)
        .order_by(desc(LeadEnrichmentProposal.created_at))
        .limit(10)
    )
    proposals = proposals_result.scalars().all()

    # Recent runs for this lead
    runs_result = await session.execute(
        select(AIAgentRun)
        .where(AIAgentRun.lead_id == lead_id)
        .order_by(desc(AIAgentRun.created_at))
        .limit(5)
    )
    recent_runs = runs_result.scalars().all()

    settings = request.app.state.settings
    configured = bool(settings.AI_API_KEY) or settings.AI_PROVIDER == "mock" or settings.QBIT_ENV == "test"

    return templates.TemplateResponse(
        request,
        "ai/lead_intelligence.html",
        _ctx(
            request, user,
            lead=lead,
            intelligence=intelligence,
            score=score,
            sales_brief=sales_brief,
            proposals=proposals,
            recent_runs=recent_runs,
            ai_configured=configured,
            page_title=f"AI Intelligence — {lead.business_name}",
        ),
    )
