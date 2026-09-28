"""Phase 9 AI Agents, Lead Intelligence & Sales Copilot Models.

Models:
- AIAgentDefinition: Configurable agent registry with model routing, allowed tools, and execution limits.
- AIAgentRun: Durable agent execution record with step-by-step state, token/cost tracking, and review gates.
- AIAgentStep: Individual execution step within an agent run with tool inputs/outputs and timing.
- LeadIntelligence: Rich business intelligence with verified category, coordinates, decision makers, and field-level provenance.
- LeadScoreRecord: Transparent, deterministic lead score (0-100) with factor contributions and manual overrides.
- SalesBrief: Tailored sales intelligence brief connecting prospect characteristics to real QBIT POS hardware.
- LeadEnrichmentProposal: Proposed CRM field updates requiring human review before application.
- AIUsageRecord: Provider token usage and cost accounting.
- Product: QBIT POS hardware and accessories catalog.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, timestamp_columns, uuid_pk
from app.models.scrape import PortableJSON


class AgentRunStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ProposalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class ProductCategory(str, enum.Enum):
    WINDOWS_POS = "WINDOWS_POS"
    ANDROID_POS = "ANDROID_POS"
    HANDY_POS = "HANDY_POS"
    THERMAL_PRINTER = "THERMAL_PRINTER"
    BARCODE_SCANNER = "BARCODE_SCANNER"
    CASH_DRAWER = "CASH_DRAWER"
    ACCESSORIES = "ACCESSORIES"


class AIAgentDefinition(Base):
    """Configured AI agent definition and version in the agent registry."""

    __tablename__ = "ai_agent_definitions"
    __table_args__ = (
        Index("ix_ai_agent_slug_org", "slug", "organization_id"),
        UniqueConstraint("slug", "version", "organization_id", name="uq_agent_slug_version_org"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    system_prompt: Mapped[str] = mapped_column(Text(), nullable=False)
    model_routing_key: Mapped[str] = mapped_column(String(50), nullable=False, default="reasoning")  # extraction | classification | scoring | reasoning
    preferred_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    allowed_tools: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    allowed_sources: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    execution_limits: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)  # max_steps, max_cost, timeout_seconds

    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    runs: Mapped[list["AIAgentRun"]] = relationship("AIAgentRun", back_populates="agent_definition", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "name": self.name,
            "slug": self.slug,
            "version": self.version,
            "description": self.description,
            "model_routing_key": self.model_routing_key,
            "preferred_model": self.preferred_model,
            "allowed_tools": self.allowed_tools or [],
            "allowed_sources": self.allowed_sources or [],
            "execution_limits": self.execution_limits or {},
            "requires_human_review": self.requires_human_review,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AIAgentRun(Base):
    """Durable agent execution run with step-by-step state and idempotency controls."""

    __tablename__ = "ai_agent_runs"
    __table_args__ = (
        Index("ix_ai_agent_runs_org", "organization_id"),
        Index("ix_ai_agent_runs_status", "status"),
        Index("ix_ai_agent_runs_lead", "lead_id"),
        Index("ix_ai_agent_runs_idempotency", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    agent_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_agent_definitions.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )

    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=AgentRunStatus.QUEUED.value)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, default=lambda: uuid.uuid4().hex)

    execution_plan: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    input_data: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    output_data: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)

    provider_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    token_usage: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)  # prompt_tokens, completion_tokens, total_tokens
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)

    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_review_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_definition: Mapped["AIAgentDefinition | None"] = relationship("AIAgentDefinition", back_populates="runs", lazy="selectin")
    steps: Mapped[list["AIAgentStep"]] = relationship("AIAgentStep", back_populates="run", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        from sqlalchemy import inspect as sa_inspect
        insp = sa_inspect(self)
        steps_list = []
        if insp is not None and "steps" not in insp.unloaded:
            steps_list = [s.to_dict() for s in (self.steps or [])]
        agent_name = self.task_name
        if insp is not None and "agent_definition" not in insp.unloaded and self.agent_definition:
            agent_name = self.agent_definition.name

        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "agent_definition_id": str(self.agent_definition_id) if self.agent_definition_id else None,
            "agent_name": agent_name,
            "lead_id": str(self.lead_id) if self.lead_id else None,
            "task_name": self.task_name,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "execution_plan": self.execution_plan or [],
            "input_data": self.input_data or {},
            "output_data": self.output_data or {},
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "token_usage": self.token_usage or {},
            "cost_estimate": round(self.cost_estimate, 4),
            "error_message": self.error_message,
            "review_required": self.review_required,
            "is_review_approved": self.is_review_approved,
            "steps": steps_list,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AIAgentStep(Base):
    """Step execution within an agent run."""

    __tablename__ = "ai_agent_steps"
    __table_args__ = (
        Index("ix_ai_agent_steps_run", "run_id"),
        Index("ix_ai_agent_steps_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_agent_runs.id", ondelete="CASCADE"), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_name: Mapped[str] = mapped_column(String(200), nullable=False)

    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_input: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    tool_output: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default=StepStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = timestamp_columns()[0]

    run: Mapped["AIAgentRun"] = relationship("AIAgentRun", back_populates="steps", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "step_index": self.step_index,
            "step_name": self.step_name,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input or {},
            "tool_output": self.tool_output or {},
            "status": self.status,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LeadIntelligence(Base):
    """Deep business intelligence with field-level provenance and source citations."""

    __tablename__ = "lead_intelligence"
    __table_args__ = (
        Index("ix_lead_intelligence_lead", "lead_id"),
        Index("ix_lead_intelligence_org", "organization_id"),
        UniqueConstraint("lead_id", name="uq_lead_intelligence_lead"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True)

    business_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    business_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_subcategory: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    verified_location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    coordinates: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)  # lat, lng, plus_code, place_id

    key_decision_makers: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    sentiment_signals: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    social_profiles: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    data_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: Field-level provenance: [{"field": "website", "source_name": "google_maps", "source_url": "...", "snippet": "...", "verified": True, "confidence": 0.95, "timestamp": "..."}]
    provenance_records: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "lead_id": str(self.lead_id),
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "business_summary": self.business_summary,
            "business_category": self.business_category,
            "business_subcategory": self.business_subcategory,
            "classification_confidence": round(self.classification_confidence, 2),
            "verified_location": self.verified_location,
            "coordinates": self.coordinates or {},
            "key_decision_makers": self.key_decision_makers or [],
            "sentiment_signals": self.sentiment_signals or {},
            "social_profiles": self.social_profiles or {},
            "data_completeness": round(self.data_completeness, 2),
            "provenance_records": self.provenance_records or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LeadScoreRecord(Base):
    """Transparent, deterministic lead score (0-100) with factor breakdown."""

    __tablename__ = "lead_score_records"
    __table_args__ = (
        Index("ix_lead_scores_lead", "lead_id"),
        Index("ix_lead_scores_org", "organization_id"),
        Index("ix_lead_scores_score", "score"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True)

    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    category_fit_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location_fit_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contact_completeness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_review_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pos_hardware_fit_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagement_history_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    factor_breakdown: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text(), nullable=True)

    is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    overridden_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = timestamp_columns()[0]

    def to_dict(self) -> dict[str, Any]:
        effective_score = self.override_score if (self.is_overridden and self.override_score is not None) else self.score
        return {
            "id": str(self.id),
            "lead_id": str(self.lead_id),
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "score": effective_score,
            "base_score": self.score,
            "confidence": round(self.confidence, 2),
            "category_fit_score": self.category_fit_score,
            "location_fit_score": self.location_fit_score,
            "contact_completeness_score": self.contact_completeness_score,
            "rating_review_score": self.rating_review_score,
            "pos_hardware_fit_score": self.pos_hardware_fit_score,
            "engagement_history_score": self.engagement_history_score,
            "factor_breakdown": self.factor_breakdown or {},
            "explanation": self.explanation,
            "is_overridden": self.is_overridden,
            "override_score": self.override_score,
            "override_reason": self.override_reason,
            "overridden_by": str(self.overridden_by) if self.overridden_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SalesBrief(Base):
    """Personalized sales research brief connecting prospect needs to QBIT product catalog."""

    __tablename__ = "sales_briefs"
    __table_args__ = (
        Index("ix_sales_briefs_lead", "lead_id"),
        Index("ix_sales_briefs_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True)

    business_overview: Mapped[str] = mapped_column(Text(), nullable=False)
    recommended_products: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    sales_opportunities: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    suggested_discovery_questions: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    possible_objections: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    recommended_next_action: Mapped[str] = mapped_column(Text(), nullable=False)
    data_gaps: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)

    created_at: Mapped[datetime] = timestamp_columns()[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "lead_id": str(self.lead_id),
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "business_overview": self.business_overview,
            "recommended_products": self.recommended_products or [],
            "sales_opportunities": self.sales_opportunities or [],
            "suggested_discovery_questions": self.suggested_discovery_questions or [],
            "possible_objections": self.possible_objections or [],
            "recommended_next_action": self.recommended_next_action,
            "data_gaps": self.data_gaps or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LeadEnrichmentProposal(Base):
    """Proposed CRM field updates from AI enrichment requiring review before merge."""

    __tablename__ = "lead_enrichment_proposals"
    __table_args__ = (
        Index("ix_enrichment_proposals_lead", "lead_id"),
        Index("ix_enrichment_proposals_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True)

    proposed_changes: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=ProposalStatus.PENDING.value)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "lead_id": str(self.lead_id),
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "proposed_changes": self.proposed_changes or {},
            "status": self.status,
            "reviewed_by": str(self.reviewed_by) if self.reviewed_by else None,
            "review_notes": self.review_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
        }


class AIUsageRecord(Base):
    """Actual provider token consumption and cost monitoring."""

    __tablename__ = "ai_usage_records"
    __table_args__ = (
        Index("ix_ai_usage_org_created", "organization_id", "created_at"),
        Index("ix_ai_usage_provider", "provider"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = timestamp_columns()[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": round(self.estimated_cost, 6),
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Product(Base):
    """QBIT POS hardware and accessories catalog for authentic sales intelligence."""

    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_sku", "sku"),
        Index("ix_products_category", "category"),
        UniqueConstraint("sku", name="uq_products_sku"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    price_inr: Mapped[float] = mapped_column(Float, nullable=False)

    specifications: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    target_industries: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "sku": self.sku,
            "category": self.category,
            "description": self.description,
            "price_inr": self.price_inr,
            "specifications": self.specifications or {},
            "target_industries": self.target_industries or [],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
