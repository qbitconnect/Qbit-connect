"""Social Post management, content composer, and approval service (Phase 8 §5, §8, §9)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.models.marketing import Campaign
from app.models.social import (
    ApprovalStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialAuditLog,
    SocialPlatform,
    SocialPost,
    SocialPostStatus,
    SocialPostTarget,
    TargetStatus,
)

logger = get_logger("qbit.social.service")

# Platform Character & Media Limits
PLATFORM_LIMITS = {
    "FACEBOOK": {"max_chars": 63206, "min_media": 0, "max_media": 10},
    "INSTAGRAM": {"max_chars": 2200, "min_media": 1, "max_media": 10, "max_hashtags": 30},
    "LINKEDIN": {"max_chars": 3000, "min_media": 0, "max_media": 9},
}


class SocialPostService:
    """Core domain service for authoring, validating, approving, and scheduling posts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Validation -----------------------------------------------------------

    def validate_content_for_platform(
        self,
        platform: str,
        caption: str,
        media_urls: list[dict[str, Any]],
    ) -> list[str]:
        """Verify copy and media satisfy the official rules of the target platform."""
        problems = []
        rules = PLATFORM_LIMITS.get(platform.upper())
        if not rules:
            return problems

        # Text length
        text_len = len(caption or "")
        if text_len > rules["max_chars"]:
            problems.append(
                f"{platform} caption exceeds limit of {rules['max_chars']} characters (current: {text_len})"
            )

        # Media requirements
        media_count = len(media_urls or [])
        if media_count < rules["min_media"]:
            problems.append(f"{platform} requires at least {rules['min_media']} media item(s). Text-only posts are unsupported.")
        if media_count > rules["max_media"]:
            problems.append(f"{platform} supports a maximum of {rules['max_media']} media items (attached: {media_count})")

        # Instagram hashtag limit
        if platform.upper() == "INSTAGRAM":
            hashtags = re.findall(r"#\w+", caption or "")
            if len(hashtags) > rules.get("max_hashtags", 30):
                problems.append(f"Instagram allows at most 30 hashtags (found: {len(hashtags)})")

        return problems

    # --- CRUD & Authoring -----------------------------------------------------

    async def create_post(
        self,
        *,
        organization_id: uuid.UUID | None,
        author_id: uuid.UUID | None,
        title: str,
        caption: str,
        account_ids: list[str | uuid.UUID],
        platform_customizations: dict[str, Any] | None = None,
        media_urls: list[dict[str, Any]] | None = None,
        scheduled_at: datetime | None = None,
        post_timezone: str = "UTC",
        campaign_id: uuid.UUID | None = None,
        as_draft: bool = True,
    ) -> SocialPost:
        """Create a new social post with target account associations."""
        if not title or not title.strip():
            raise ValidationError("Internal post title is required")
        if not caption or not caption.strip():
            raise ValidationError("Post caption is required")
        if not account_ids:
            raise ValidationError("At least one social account must be targeted")

        # Validate accounts exist and belong to tenant
        accounts_query = select(SocialAccount).where(
            SocialAccount.id.in_([uuid.UUID(str(aid)) for aid in account_ids])
        )
        if organization_id:
            accounts_query = accounts_query.where(
                (SocialAccount.organization_id == organization_id) | (SocialAccount.organization_id.is_(None))
            )
        target_accounts = (await self.session.scalars(accounts_query)).all()
        if len(target_accounts) != len(account_ids):
            raise ValidationError("One or more selected social accounts were not found or unauthorized")

        # Validate content for each platform
        customizations = platform_customizations or {}
        media = media_urls or []
        for acc in target_accounts:
            platform_caption = customizations.get(acc.platform, {}).get("caption", caption)
            problems = self.validate_content_for_platform(acc.platform, platform_caption, media)
            if problems and not as_draft:
                raise ValidationError(f"Validation failure for {acc.platform}: {'; '.join(problems)}")

        # Validate CRM campaign link if provided
        if campaign_id:
            camp = await self.session.get(Campaign, campaign_id)
            if not camp:
                raise ValidationError("Linked CRM campaign not found")

        status = SocialPostStatus.DRAFT.value if as_draft else (
            SocialPostStatus.SCHEDULED.value if scheduled_at else SocialPostStatus.APPROVED.value
        )
        approval_status = ApprovalStatus.DRAFT.value if as_draft else ApprovalStatus.APPROVED.value

        post = SocialPost(
            organization_id=organization_id,
            author_id=author_id,
            campaign_id=campaign_id,
            title=title.strip(),
            caption=caption.strip(),
            platform_customizations=customizations,
            media_urls=media,
            status=status,
            approval_status=approval_status,
            scheduled_at=scheduled_at,
            timezone=post_timezone or "UTC",
        )
        self.session.add(post)
        await self.session.flush()

        # Create targets
        for acc in target_accounts:
            target = SocialPostTarget(
                post_id=post.id,
                social_account_id=acc.id,
                platform=acc.platform,
                status=TargetStatus.PENDING.value if as_draft else TargetStatus.QUEUED.value,
                idempotency_key=f"target_{post.id}_{acc.id}_{uuid.uuid4().hex[:8]}",
            )
            self.session.add(target)

        # Audit log
        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=author_id,
            event_type="CREATED",
            details={"title": post.title, "status": post.status, "accounts_count": len(target_accounts)},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post, ["targets", "audit_logs"])
        return post

    async def duplicate_post(self, post_id: uuid.UUID, actor_id: uuid.UUID | None) -> SocialPost:
        """Duplicate an existing post into a fresh draft."""
        original = await self.get_post_or_404(post_id)
        # Fetch targets
        target_account_ids = [t.social_account_id for t in original.targets]

        new_post = await self.create_post(
            organization_id=original.organization_id,
            author_id=actor_id,
            title=f"Copy of {original.title}",
            caption=original.caption,
            account_ids=target_account_ids,
            platform_customizations=dict(original.platform_customizations or {}),
            media_urls=list(original.media_urls or []),
            scheduled_at=None,
            post_timezone=original.timezone,
            campaign_id=original.campaign_id,
            as_draft=True,
        )
        return new_post

    async def update_draft(
        self,
        post_id: uuid.UUID,
        *,
        title: str | None = None,
        caption: str | None = None,
        platform_customizations: dict[str, Any] | None = None,
        media_urls: list[dict[str, Any]] | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> SocialPost:
        post = await self.get_post_or_404(post_id)
        if post.status in (SocialPostStatus.PUBLISHING.value, SocialPostStatus.PUBLISHED.value):
            raise ValidationError("Cannot edit a post that is currently publishing or already published")

        if title is not None:
            post.title = title.strip()
        if caption is not None:
            post.caption = caption.strip()
        if platform_customizations is not None:
            post.platform_customizations = platform_customizations
        if media_urls is not None:
            post.media_urls = media_urls

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=actor_id,
            event_type="EDITED",
            details={"updated_fields": ["title", "caption"]},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def get_post_or_404(self, post_id: uuid.UUID) -> SocialPost:
        stmt = (
            select(SocialPost)
            .where(SocialPost.id == post_id)
            .options(selectinload(SocialPost.targets), selectinload(SocialPost.audit_logs))
        )
        post = await self.session.scalar(stmt)
        if not post:
            raise NotFoundError(f"Social post {post_id} not found")
        return post

    async def list_posts(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        status: str | None = None,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialPost]:
        stmt = select(SocialPost).order_by(desc(SocialPost.created_at))
        if organization_id:
            stmt = stmt.where((SocialPost.organization_id == organization_id) | (SocialPost.organization_id.is_(None)))
        if status:
            stmt = stmt.where(SocialPost.status == status.upper())
        if platform:
            stmt = stmt.join(SocialPostTarget).where(SocialPostTarget.platform == platform.upper())
        stmt = stmt.limit(limit).offset(offset)
        return (await self.session.scalars(stmt)).unique().all()

    # --- Approval Workflow ----------------------------------------------------

    async def submit_for_approval(self, post_id: uuid.UUID, actor_id: uuid.UUID | None) -> SocialPost:
        post = await self.get_post_or_404(post_id)
        if post.status != SocialPostStatus.DRAFT.value:
            raise ValidationError(f"Only DRAFT posts can be submitted for approval (current: {post.status})")

        post.status = SocialPostStatus.PENDING_APPROVAL.value
        post.approval_status = ApprovalStatus.PENDING.value
        post.rejection_reason = None

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=actor_id,
            event_type="SUBMITTED_FOR_APPROVAL",
            details={},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def approve_post(self, post_id: uuid.UUID, approver_id: uuid.UUID | None) -> SocialPost:
        post = await self.get_post_or_404(post_id)
        if post.status != SocialPostStatus.PENDING_APPROVAL.value:
            raise ValidationError(f"Post is not pending approval (status: {post.status})")

        post.approval_status = ApprovalStatus.APPROVED.value
        post.approver_id = approver_id
        post.rejection_reason = None

        # If scheduled_at is set, transition to SCHEDULED; otherwise APPROVED (ready to dispatch)
        if post.scheduled_at:
            post.status = SocialPostStatus.SCHEDULED.value
        else:
            post.status = SocialPostStatus.APPROVED.value

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=approver_id,
            event_type="APPROVED",
            details={"approved_by": str(approver_id) if approver_id else None},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def reject_post(self, post_id: uuid.UUID, approver_id: uuid.UUID | None, reason: str) -> SocialPost:
        if not reason or not reason.strip():
            raise ValidationError("A rejection reason is required")
        post = await self.get_post_or_404(post_id)
        if post.status != SocialPostStatus.PENDING_APPROVAL.value:
            raise ValidationError("Post is not pending approval")

        post.status = SocialPostStatus.REJECTED.value
        post.approval_status = ApprovalStatus.REJECTED.value
        post.approver_id = approver_id
        post.rejection_reason = reason.strip()

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=approver_id,
            event_type="REJECTED",
            details={"reason": reason.strip()},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    # --- Scheduling & Cancellation --------------------------------------------

    async def schedule_post(
        self,
        post_id: uuid.UUID,
        scheduled_at: datetime,
        post_timezone: str,
        actor_id: uuid.UUID | None,
    ) -> SocialPost:
        post = await self.get_post_or_404(post_id)
        if post.status in (SocialPostStatus.PUBLISHING.value, SocialPostStatus.PUBLISHED.value):
            raise ValidationError("Cannot schedule a post that is already publishing or published")

        now = datetime.now(timezone.utc)
        sched_utc = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)
        if sched_utc <= now:
            raise ValidationError("Scheduled time must be in the future")

        post.scheduled_at = sched_utc
        post.timezone = post_timezone or "UTC"
        # If approval is not pending, set to SCHEDULED
        if post.approval_status == ApprovalStatus.APPROVED.value:
            post.status = SocialPostStatus.SCHEDULED.value

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=actor_id,
            event_type="SCHEDULED",
            details={"scheduled_at": sched_utc.isoformat(), "timezone": post.timezone},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def cancel_post(self, post_id: uuid.UUID, actor_id: uuid.UUID | None) -> SocialPost:
        """Cancel a scheduled post before dispatch."""
        post = await self.get_post_or_404(post_id)
        if post.status == SocialPostStatus.PUBLISHED.value:
            raise ValidationError("Cannot cancel a post that has already been published")
        if post.status == SocialPostStatus.PUBLISHING.value:
            raise ValidationError("Cannot cancel a post that is actively being dispatched to platforms")

        post.status = SocialPostStatus.CANCELLED.value
        # Mark targets cancelled
        for t in post.targets:
            if t.status in (TargetStatus.PENDING.value, TargetStatus.QUEUED.value):
                t.status = TargetStatus.CANCELLED.value

        audit = SocialAuditLog(
            post_id=post.id,
            actor_id=actor_id,
            event_type="CANCELLED",
            details={},
        )
        self.session.add(audit)
        await self.session.commit()
        await self.session.refresh(post)
        return post

    async def delete_post(self, post_id: uuid.UUID, actor_id: uuid.UUID | None) -> None:
        post = await self.get_post_or_404(post_id)
        if post.status in (SocialPostStatus.PUBLISHING.value, SocialPostStatus.PUBLISHED.value):
            raise ValidationError("Cannot delete a post that is publishing or published")
        await self.session.delete(post)
        await self.session.commit()
