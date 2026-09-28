"""Follow-up tasks and reminder scheduling for CRM leads (Phase 5)."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.lead import (
    FollowUpPriority,
    FollowUpStatus,
    LeadFollowUp,
)
from app.models.scrape import Lead
from app.services.leads.activity import LeadActivityService


class FollowUpService:
    def __init__(self, activities: LeadActivityService | None = None):
        self.activities = activities or LeadActivityService()

    async def create(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
        *,
        title: str,
        due_at: datetime,
        notes: str | None = None,
        priority: str = FollowUpPriority.MEDIUM.value,
        assigned_user_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> LeadFollowUp:
        title_clean = (title or "").strip()
        if not title_clean:
            raise ValidationError("Task title is required")

        lead = await session.get(Lead, lead_id)
        if not lead:
            raise NotFoundError("Lead not found")

        valid_priorities = {p.value for p in FollowUpPriority}
        prio_norm = priority.upper() if priority else FollowUpPriority.MEDIUM.value
        if prio_norm not in valid_priorities:
            prio_norm = FollowUpPriority.MEDIUM.value

        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)

        org_id = organization_id or lead.organization_id
        assignee = assigned_user_id or lead.assigned_user_id

        task = LeadFollowUp(
            lead_id=lead.id,
            organization_id=org_id,
            assigned_user_id=assignee,
            created_by=created_by,
            title=title_clean,
            notes=notes.strip() if notes else None,
            due_at=due_at,
            status=FollowUpStatus.PENDING.value,
            priority=prio_norm,
        )
        session.add(task)

        now = datetime.now(timezone.utc)
        lead.last_activity_at = now
        lead.updated_at = now

        await self.activities.log(
            session,
            lead.id,
            "follow_up_created",
            message=f"Follow-up scheduled: {title_clean} (Due: {due_at.strftime('%Y-%m-%d %H:%M')})",
            metadata={"task_id": str(task.id), "due_at": due_at.isoformat(), "priority": prio_norm},
            user_id=created_by,
            commit=False,
        )

        if commit:
            await session.commit()
            await session.refresh(task)

        return task

    async def list_for_lead(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[LeadFollowUp]:
        query = select(LeadFollowUp).where(LeadFollowUp.lead_id == lead_id)
        if status:
            query = query.where(LeadFollowUp.status == status.upper())
        query = query.order_by(LeadFollowUp.due_at.asc())
        res = await session.execute(query)
        return list(res.scalars().all())

    async def list_user_tasks(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        status: str | None = None,
        overdue_only: bool = False,
        due_today_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LeadFollowUp], int]:
        now = datetime.now(timezone.utc)
        query = select(LeadFollowUp)

        if organization_id is not None:
            query = query.where(LeadFollowUp.organization_id == organization_id)

        if user_id is not None:
            query = query.where(
                or_(
                    LeadFollowUp.assigned_user_id == user_id,
                    LeadFollowUp.created_by == user_id,
                )
            )

        if status:
            query = query.where(LeadFollowUp.status == status.upper())

        if overdue_only:
            query = query.where(
                LeadFollowUp.status == FollowUpStatus.PENDING.value,
                LeadFollowUp.due_at < now,
            )
        elif due_today_only:
            start_of_day = datetime.combine(now.date(), time.min).replace(tzinfo=timezone.utc)
            end_of_day = datetime.combine(now.date(), time.max).replace(tzinfo=timezone.utc)
            query = query.where(
                LeadFollowUp.status == FollowUpStatus.PENDING.value,
                LeadFollowUp.due_at >= start_of_day,
                LeadFollowUp.due_at <= end_of_day,
            )

        count_query = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_query) or 0

        query = query.order_by(LeadFollowUp.due_at.asc()).offset(offset).limit(limit)
        res = await session.execute(query)
        return list(res.scalars().all()), int(total)

    async def complete(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> LeadFollowUp:
        task = await session.get(LeadFollowUp, task_id)
        if not task:
            raise NotFoundError("Follow-up task not found")

        now = datetime.now(timezone.utc)
        task.status = FollowUpStatus.COMPLETED.value
        task.completed_at = now
        task.updated_at = now

        lead = await session.get(Lead, task.lead_id)
        if lead:
            lead.last_activity_at = now
            lead.updated_at = now
            await self.activities.log(
                session,
                lead.id,
                "follow_up_completed",
                message=f"Follow-up task completed: {task.title}",
                metadata={"task_id": str(task.id)},
                user_id=user_id,
                commit=False,
            )

        if commit:
            await session.commit()
            await session.refresh(task)

        return task

    async def cancel(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> LeadFollowUp:
        task = await session.get(LeadFollowUp, task_id)
        if not task:
            raise NotFoundError("Follow-up task not found")

        now = datetime.now(timezone.utc)
        task.status = FollowUpStatus.CANCELLED.value
        task.cancelled_at = now
        task.updated_at = now

        lead = await session.get(Lead, task.lead_id)
        if lead:
            lead.last_activity_at = now
            lead.updated_at = now
            await self.activities.log(
                session,
                lead.id,
                "follow_up_cancelled",
                message=f"Follow-up task cancelled: {task.title}",
                metadata={"task_id": str(task.id)},
                user_id=user_id,
                commit=False,
            )

        if commit:
            await session.commit()
            await session.refresh(task)

        return task
