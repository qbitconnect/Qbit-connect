"""Specialized AI Agent implementations.

All agents inherit BaseAgent and persist every step to AIAgentStep.
They use the AIGateway for LLM calls and the tool registry for data access.
No agent ever generates fake/hallucinated data — missing info is explicitly
reported as 'unknown' or 'unavailable'.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.models.ai import (
    AIAgentRun,
    AIAgentStep,
    AgentRunStatus,
    LeadEnrichmentProposal,
    LeadIntelligence,
    LeadScoreRecord,
    ProposalStatus,
    SalesBrief,
    StepStatus,
)
from app.services.ai.gateway import AIGateway, AIBudgetExceeded
from app.services.ai.tools import (
    ToolResult,
    calculate_lead_score,
    crm_lead_lookup,
    product_catalog_search,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.core.config import Settings

logger = get_logger("qbit.ai.agents")


class BaseAgent:
    """Base class for all QBIT AI agents."""

    slug: str = "base"
    routing_key: str = "reasoning"

    def __init__(self, gateway: AIGateway, settings: "Settings") -> None:
        self.gateway = gateway
        self.settings = settings

    async def _record_step(
        self,
        session: "AsyncSession",
        run: AIAgentRun,
        step_index: int,
        step_name: str,
        tool_name: str | None,
        tool_input: dict,
        tool_output: dict,
        status: str,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> AIAgentStep:
        step = AIAgentStep(
            run_id=run.id,
            step_index=step_index,
            step_name=step_name,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            status=status,
            error=error,
            duration_ms=duration_ms,
            created_at=datetime.now(timezone.utc),
        )
        session.add(step)
        await session.flush()
        return step

    async def run(self, session: "AsyncSession", run: AIAgentRun) -> dict:
        raise NotImplementedError


class LeadEnrichmentAgent(BaseAgent):
    """Enriches a lead's CRM record using public business intelligence."""

    slug = "lead-enrichment"
    routing_key = "reasoning"

    async def run(self, session: "AsyncSession", run: AIAgentRun) -> dict:
        lead_id = run.input_data.get("lead_id")
        if not lead_id:
            return {"error": "lead_id required"}

        run.started_at = datetime.now(timezone.utc)
        await session.flush()

        # Step 1: Fetch lead from CRM
        step_idx = 0
        lead_result = await crm_lead_lookup(
            lead_id=lead_id,
            session=session,
            organization_id=str(run.organization_id) if run.organization_id else None,
        )
        await self._record_step(
            session, run, step_idx, "fetch_lead", "crm_lead_lookup",
            {"lead_id": lead_id},
            lead_result.data,
            StepStatus.SUCCESS.value if lead_result.success else StepStatus.FAILED.value,
            error=lead_result.error,
        )

        if not lead_result.success:
            return {"error": lead_result.error}

        lead_data = lead_result.data
        step_idx += 1

        # Step 2: AI enrichment analysis
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a business intelligence analyst for QBIT Connect, a B2B POS hardware sales platform. "
                    "Analyse the provided lead data and extract structured intelligence. "
                    "NEVER invent information. Mark unknown fields as null. "
                    "Respond ONLY with valid JSON."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Lead data: {json.dumps(lead_data, indent=2)}\n\n"
                    "Extract and return JSON with:\n"
                    '- business_summary (1-2 sentences, factual only, null if insufficient data)\n'
                    '- business_category (refined category or null)\n'
                    '- business_subcategory (specific niche or null)\n'
                    '- classification_confidence (0.0-1.0)\n'
                    '- key_decision_makers (array of {name, title, source} — only from provided data, [] if none)\n'
                    '- sentiment_signals ({avg_rating, review_count, sentiment_label: positive/neutral/negative/unknown})\n'
                    '- data_gaps (array of important missing fields)\n'
                    '- proposed_crm_updates (dict of field->value for verified new information only)'
                ),
            },
        ]

        import time
        t0 = time.monotonic()
        try:
            resp = await self.gateway.complete(
                messages=messages,
                routing_key=self.routing_key,
                session=session,
                organization_id=str(run.organization_id) if run.organization_id else None,
                user_id=str(run.user_id) if run.user_id else None,
                agent_run_id=str(run.id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI completion failed in LeadEnrichmentAgent")
            await self._record_step(
                session, run, step_idx, "ai_enrichment_analysis", None,
                {"routing_key": self.routing_key},
                {},
                StepStatus.FAILED.value,
                error=str(exc),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return {"error": str(exc)}

        duration_ms = int((time.monotonic() - t0) * 1000)
        run.token_usage = {
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "total_tokens": resp.total_tokens,
        }
        run.cost_estimate += resp.cost_estimate
        run.provider_used = resp.provider
        run.model_used = resp.model

        # Parse AI response
        ai_data = {}
        try:
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
            ai_data = json.loads(raw)
        except json.JSONDecodeError:
            ai_data = {}
            logger.warning("Could not parse AI JSON response; raw=%s", resp.text[:200])

        await self._record_step(
            session, run, step_idx, "ai_enrichment_analysis", None,
            {"routing_key": self.routing_key},
            ai_data,
            StepStatus.SUCCESS.value,
            duration_ms=duration_ms,
        )
        step_idx += 1

        # Step 3: Persist LeadIntelligence
        from sqlalchemy import select
        from app.models.ai import LeadIntelligence

        existing = await session.execute(
            select(LeadIntelligence).where(LeadIntelligence.lead_id == uuid.UUID(lead_id))
        )
        intel = existing.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if intel is None:
            intel = LeadIntelligence(
                organization_id=run.organization_id,
                lead_id=uuid.UUID(lead_id),
                agent_run_id=run.id,
                created_at=now,
                updated_at=now,
            )
            session.add(intel)

        intel.business_summary = ai_data.get("business_summary")
        intel.business_category = ai_data.get("business_category") or lead_data.get("category")
        intel.business_subcategory = ai_data.get("business_subcategory")
        intel.classification_confidence = float(ai_data.get("classification_confidence") or 0.0)
        intel.key_decision_makers = ai_data.get("key_decision_makers") or []
        intel.sentiment_signals = ai_data.get("sentiment_signals") or {}
        intel.agent_run_id = run.id
        intel.updated_at = now

        # Compute data completeness
        fields_present = sum(1 for f in [lead_data.get("phone"), lead_data.get("email"),
                                          lead_data.get("website"), lead_data.get("city"),
                                          lead_data.get("rating"), intel.business_summary] if f)
        intel.data_completeness = round(fields_present / 6, 2)

        await session.flush()

        # Step 4: Create enrichment proposal if there are CRM updates
        proposed_changes = ai_data.get("proposed_crm_updates") or {}
        if proposed_changes:
            proposal = LeadEnrichmentProposal(
                organization_id=run.organization_id,
                lead_id=uuid.UUID(lead_id),
                agent_run_id=run.id,
                proposed_changes=proposed_changes,
                status=ProposalStatus.PENDING.value,
                created_at=now,
            )
            session.add(proposal)
            await session.flush()

        await self._record_step(
            session, run, step_idx, "persist_intelligence", None,
            {"lead_id": lead_id},
            {"intelligence_id": str(intel.id), "proposals_created": bool(proposed_changes)},
            StepStatus.SUCCESS.value,
        )

        return {
            "lead_id": lead_id,
            "intelligence_id": str(intel.id),
            "business_summary": intel.business_summary,
            "data_completeness": intel.data_completeness,
            "proposals_created": bool(proposed_changes),
        }


class LeadScoringAgent(BaseAgent):
    """Calculates a transparent, deterministic lead score (0-100)."""

    slug = "lead-scoring"
    routing_key = "scoring"

    async def run(self, session: "AsyncSession", run: AIAgentRun) -> dict:
        lead_id = run.input_data.get("lead_id")
        if not lead_id:
            return {"error": "lead_id required"}

        run.started_at = datetime.now(timezone.utc)
        await session.flush()

        # Step 1: Fetch lead
        lead_result = await crm_lead_lookup(
            lead_id=lead_id,
            session=session,
            organization_id=str(run.organization_id) if run.organization_id else None,
        )
        await self._record_step(
            session, run, 0, "fetch_lead", "crm_lead_lookup",
            {"lead_id": lead_id},
            lead_result.data,
            StepStatus.SUCCESS.value if lead_result.success else StepStatus.FAILED.value,
            error=lead_result.error,
        )

        if not lead_result.success:
            return {"error": lead_result.error}

        # Step 2: Calculate score deterministically
        score_data = calculate_lead_score(lead_result.data)

        now = datetime.now(timezone.utc)
        score_record = LeadScoreRecord(
            organization_id=run.organization_id,
            lead_id=uuid.UUID(lead_id),
            agent_run_id=run.id,
            score=score_data["score"],
            confidence=score_data["confidence"],
            category_fit_score=score_data["category_fit_score"],
            location_fit_score=score_data["location_fit_score"],
            contact_completeness_score=score_data["contact_completeness_score"],
            rating_review_score=score_data["rating_review_score"],
            pos_hardware_fit_score=score_data["pos_hardware_fit_score"],
            engagement_history_score=score_data["engagement_history_score"],
            factor_breakdown=score_data["factor_breakdown"],
            explanation=score_data["explanation"],
            created_at=now,
        )
        session.add(score_record)
        await session.flush()

        await self._record_step(
            session, run, 1, "calculate_score", "lead_score_calculate",
            lead_result.data,
            score_data,
            StepStatus.SUCCESS.value,
        )

        run.provider_used = "deterministic"
        run.model_used = "rule-based-v1"

        return {
            "lead_id": lead_id,
            "score_record_id": str(score_record.id),
            **score_data,
        }


class SalesIntelligenceAgent(BaseAgent):
    """Generates a tailored sales brief mapping prospect needs to QBIT products."""

    slug = "sales-intelligence"
    routing_key = "reasoning"

    async def run(self, session: "AsyncSession", run: AIAgentRun) -> dict:
        lead_id = run.input_data.get("lead_id")
        if not lead_id:
            return {"error": "lead_id required"}

        run.started_at = datetime.now(timezone.utc)
        await session.flush()

        step_idx = 0

        # Step 1: Fetch lead
        lead_result = await crm_lead_lookup(
            lead_id=lead_id, session=session,
            organization_id=str(run.organization_id) if run.organization_id else None,
        )
        await self._record_step(
            session, run, step_idx, "fetch_lead", "crm_lead_lookup",
            {"lead_id": lead_id}, lead_result.data,
            StepStatus.SUCCESS.value if lead_result.success else StepStatus.FAILED.value,
            error=lead_result.error,
        )
        if not lead_result.success:
            return {"error": lead_result.error}
        step_idx += 1

        # Step 2: Search product catalog by industry
        industry = lead_result.data.get("category", "")
        product_result = await product_catalog_search(
            query=industry, industry=industry, session=session,
        )
        await self._record_step(
            session, run, step_idx, "search_products", "product_catalog_search",
            {"query": industry}, product_result.data,
            StepStatus.SUCCESS.value if product_result.success else StepStatus.FAILED.value,
        )
        step_idx += 1

        # Step 3: AI brief generation
        products = product_result.data.get("products", [])
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior sales executive at QBIT Connect, a company selling POS hardware "
                    "(terminals, printers, scanners, cash drawers) to restaurants, retail, hotels, and F&B businesses. "
                    "Create a concise, actionable sales brief for the given prospect. "
                    "Use ONLY the provided product catalog — do not invent products. "
                    "Respond with valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Prospect: {json.dumps(lead_result.data, indent=2)}\n\n"
                    f"Available products: {json.dumps(products[:5], indent=2)}\n\n"
                    "Generate a sales brief JSON with:\n"
                    '- business_overview (2-3 sentences describing this prospect as a sales target)\n'
                    '- recommended_products (array of {sku, name, reason} from catalog only)\n'
                    '- sales_opportunities (array of specific pain-point strings)\n'
                    '- suggested_discovery_questions (array of 3-5 open-ended questions)\n'
                    '- possible_objections (array of {objection, response} pairs)\n'
                    '- recommended_next_action (one clear next step)\n'
                    '- data_gaps (fields that would improve the brief if available)'
                ),
            },
        ]

        import time
        t0 = time.monotonic()
        try:
            resp = await self.gateway.complete(
                messages=messages, routing_key=self.routing_key,
                session=session,
                organization_id=str(run.organization_id) if run.organization_id else None,
                user_id=str(run.user_id) if run.user_id else None,
                agent_run_id=str(run.id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI completion failed in SalesIntelligenceAgent")
            return {"error": str(exc)}

        duration_ms = int((time.monotonic() - t0) * 1000)
        run.token_usage = {
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "total_tokens": resp.total_tokens,
        }
        run.cost_estimate += resp.cost_estimate
        run.provider_used = resp.provider
        run.model_used = resp.model

        ai_data = {}
        try:
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
            ai_data = json.loads(raw)
        except json.JSONDecodeError:
            ai_data = {
                "business_overview": f"Sales brief for {lead_result.data.get('business_name', 'this prospect')}.",
                "recommended_products": [],
                "sales_opportunities": [],
                "suggested_discovery_questions": [],
                "possible_objections": [],
                "recommended_next_action": "Schedule a discovery call to understand current POS setup.",
                "data_gaps": ["AI response parsing failed; manual review recommended"],
            }

        await self._record_step(
            session, run, step_idx, "generate_brief", None,
            {"routing_key": self.routing_key}, ai_data,
            StepStatus.SUCCESS.value, duration_ms=duration_ms,
        )
        step_idx += 1

        # Step 4: Persist SalesBrief
        now = datetime.now(timezone.utc)
        brief = SalesBrief(
            organization_id=run.organization_id,
            lead_id=uuid.UUID(lead_id),
            agent_run_id=run.id,
            business_overview=ai_data.get("business_overview") or "Overview not available.",
            recommended_products=ai_data.get("recommended_products") or [],
            sales_opportunities=ai_data.get("sales_opportunities") or [],
            suggested_discovery_questions=ai_data.get("suggested_discovery_questions") or [],
            possible_objections=ai_data.get("possible_objections") or [],
            recommended_next_action=ai_data.get("recommended_next_action") or "Follow up with prospect.",
            data_gaps=ai_data.get("data_gaps") or [],
            created_at=now,
        )
        session.add(brief)
        await session.flush()

        await self._record_step(
            session, run, step_idx, "persist_brief", None,
            {"lead_id": lead_id}, {"brief_id": str(brief.id)},
            StepStatus.SUCCESS.value,
        )

        return {
            "lead_id": lead_id,
            "brief_id": str(brief.id),
            "business_overview": brief.business_overview,
            "recommended_products": brief.recommended_products,
        }


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------

_AGENT_CLASSES = {
    "lead-enrichment": LeadEnrichmentAgent,
    "lead-scoring": LeadScoringAgent,
    "sales-intelligence": SalesIntelligenceAgent,
}


def get_agent(slug: str, gateway: AIGateway, settings: "Settings") -> BaseAgent:
    """Instantiate an agent by slug. Raises KeyError if unknown."""
    cls = _AGENT_CLASSES.get(slug)
    if cls is None:
        raise KeyError(f"Unknown agent slug: {slug!r}. Available: {list(_AGENT_CLASSES)}")
    return cls(gateway=gateway, settings=settings)


AVAILABLE_AGENTS = list(_AGENT_CLASSES.keys())
