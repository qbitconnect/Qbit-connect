"""CRM Pipeline Engine (Phase 5).

Governs lead pipeline stage progression, validation of stage transitions,
recording of status transition history, and audit trails.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.lead import (
    LeadStatus,
    LeadStatusHistory,
    PIPELINE_STAGES,
)
from app.models.scrape import Lead
from app.services.leads.activity import EVENT_STATUS_CHANGED, LeadActivityService

# Default progression graph. Normal forward steps and terminal stages.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"ASSIGNED", "CONTACTED", "QUALIFIED", "NOT_INTERESTED", "LOST", "ARCHIVED"},
    "ASSIGNED": {"CONTACTED", "FOLLOW_UP", "QUALIFIED", "NOT_INTERESTED", "LOST", "NEW", "ARCHIVED"},
    "CONTACTED": {"FOLLOW_UP", "QUALIFIED", "CONVERTED", "NOT_INTERESTED", "LOST", "ASSIGNED", "ARCHIVED"},
    "FOLLOW_UP": {"CONTACTED", "QUALIFIED", "CONVERTED", "NOT_INTERESTED", "LOST", "ASSIGNED", "ARCHIVED"},
    "QUALIFIED": {"CONVERTED", "FOLLOW_UP", "NOT_INTERESTED", "LOST", "CONTACTED", "ARCHIVED"},
    "CONVERTED": {"FOLLOW_UP", "LOST", "ARCHIVED"},
    "NOT_INTERESTED": {"FOLLOW_UP", "LOST", "NEW", "ARCHIVED"},
    "LOST": {"NEW", "FOLLOW_UP", "ARCHIVED"},
    "ARCHIVED": {"NEW", "ASSIGNED"},
}


def validate_stage_transition(
    current_stage: str | None,
    target_stage: str,
    *,
    is_admin_or_manager: bool = False,
    reason: str | None = None,
) -> tuple[bool, str | None]:
    """Validate whether current_stage can transition to target_stage."""
    cur = (current_stage or "NEW").upper()
    tgt = target_stage.upper()

    if cur == tgt:
        return True, None

    # Check target is valid stage or allowed status
    valid_stages = set(PIPELINE_STAGES) | {s.value for s in LeadStatus}
    if tgt not in valid_stages:
        return False, f"Target stage '{tgt}' is not a valid pipeline stage or lead status."

    # If within default permitted transitions, approve
    allowed_for_cur = ALLOWED_TRANSITIONS.get(cur)
    if allowed_for_cur and tgt in allowed_for_cur:
        return True, None

    # If outside default transitions:
    # Custom or legacy status can transition to any valid pipeline stage
    if cur not in ALLOWED_TRANSITIONS:
        return True, None

    # Admins/Managers can override non-standard jumps if reason is provided
    if is_admin_or_manager:
        if reason and len(reason.strip()) >= 5:
            return True, None
        return (
            False,
            f"Non-standard stage jump from {cur} to {tgt} requires an explanatory reason (min 5 characters).",
        )

    return (
        False,
        f"Invalid stage transition from {cur} to {tgt}. Standard workflow does not allow direct jump without manager override.",
    )


class PipelineService:
    def __init__(self, activities: LeadActivityService | None = None):
        self.activities = activities or LeadActivityService()

    async def change_stage(
        self,
        session: AsyncSession,
        lead: Lead,
        target_stage: str,
        *,
        user_id: uuid.UUID | None = None,
        is_admin_or_manager: bool = False,
        reason: str | None = None,
        commit: bool = True,
    ) -> Lead:
        """Execute a validated stage transition, logging history and activities."""
        target_norm = target_stage.upper()
        if lead.status == target_norm:
            return lead

        is_valid, err = validate_stage_transition(
            lead.status,
            target_norm,
            is_admin_or_manager=is_admin_or_manager,
            reason=reason,
        )
        if not is_valid:
            raise ValidationError(err or "Invalid stage transition")

        old_status = lead.status
        now = datetime.now(timezone.utc)
        lead.status = target_norm
        lead.last_activity_at = now
        lead.updated_at = now

        # Add history record
        history = LeadStatusHistory(
            lead_id=lead.id,
            organization_id=getattr(lead, "organization_id", None),
            previous_status=old_status,
            new_status=target_norm,
            changed_by=user_id,
            reason=reason,
            created_at=now,
        )
        session.add(history)

        # Log lead activity
        await self.activities.log(
            session,
            lead.id,
            EVENT_STATUS_CHANGED,
            message=f"Pipeline stage changed from {old_status} to {target_norm}"
            + (f": {reason}" if reason else ""),
            metadata={"previous_status": old_status, "new_status": target_norm, "reason": reason},
            user_id=user_id,
            commit=False,
        )

        if commit:
            await session.commit()
            await session.refresh(lead)

        return lead
