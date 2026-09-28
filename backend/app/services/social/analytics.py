"""Social Media Analytics & Calendar aggregation service (Phase 8 §8, §10).

Features:
- Real post analytics synchronization from official provider APIs
- Zero data hallucination: missing metrics explicitly flagged as unavailable
- Content calendar queries grouped by date and platform
- Cross-platform reach and engagement aggregation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.social import (
    SocialAccount,
    SocialPost,
    SocialPostStatus,
    SocialPostTarget,
    TargetStatus,
)
from app.services.marketing.providers import MarketingProviderRegistry

logger = get_logger("qbit.social.analytics")


class SocialAnalyticsService:
    def __init__(
        self,
        session: AsyncSession,
        provider_registry: MarketingProviderRegistry,
        settings: Settings,
    ) -> None:
        self.session = session
        self.registry = provider_registry
        self.settings = settings

    async def sync_post_analytics(self, target_id: uuid.UUID) -> dict[str, Any]:
        """Fetch and persist real provider metrics for a published target."""
        target = await self.session.get(SocialPostTarget, target_id)
        if not target or not target.provider_post_id:
            return {"synced": False, "error": "Target not found or not yet published"}

        account = target.social_account
        if not account:
            return {"synced": False, "error": "Linked social account missing"}

        # Resolve provider
        platform_key = target.platform.upper()
        if self.settings.QBIT_ENV == "test" or self.settings.QBIT_MARKETING_ALLOW_MOCK_PROVIDER:
            provider = self.registry.get(platform_key) or self.registry.get("social_mock")
        elif platform_key == "FACEBOOK":
            provider = self.registry.get("meta_facebook")
        elif platform_key == "INSTAGRAM":
            provider = self.registry.get("instagram_graph")
        elif platform_key == "LINKEDIN":
            provider = self.registry.get("linkedin")
        else:
            provider = None

        if not provider:
            return {"synced": False, "error": f"Provider for '{target.platform}' not configured"}

        account_config = {
            "page_id": account.account_id,
            "instagram_business_account_id": account.account_id,
            "author_urn": account.account_id,
        }
        credentials = {"access_token": account.encrypted_credentials or "mock_token"}

        try:
            analytics_res = await provider.get_post_analytics(
                account_config=account_config,
                provider_post_id=target.provider_post_id,
                credentials=credentials,
            )
            now = datetime.now(timezone.utc)
            if analytics_res.available:
                target.analytics = analytics_res.metrics
                target.analytics_updated_at = now
                await self.session.commit()
                return {"synced": True, "metrics": analytics_res.metrics, "retrieved_at": now.isoformat()}
            return {"synced": False, "error": analytics_res.error_message}
        except Exception as exc:
            logger.warning(f"Failed to sync analytics for target {target_id}: {exc}")
            return {"synced": False, "error": str(exc)}

    async def get_overview_metrics(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Aggregate total published posts, reach, impressions, and engagement."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Query published targets
        stmt = (
            select(SocialPostTarget)
            .join(SocialPost)
            .where(
                SocialPostTarget.status == TargetStatus.PUBLISHED.value,
                SocialPostTarget.published_at >= since,
            )
        )
        if organization_id:
            stmt = stmt.where(SocialPost.organization_id == organization_id)

        targets = (await self.session.scalars(stmt)).all()

        total_posts = len(targets)
        total_impressions = 0
        total_reach = 0
        total_reactions = 0
        total_comments = 0
        total_shares = 0
        total_clicks = 0
        platform_breakdown: dict[str, int] = {}

        for t in targets:
            platform_breakdown[t.platform] = platform_breakdown.get(t.platform, 0) + 1
            metrics = t.analytics or {}
            total_impressions += int(metrics.get("impressions") or 0)
            total_reach += int(metrics.get("reach") or 0)
            total_reactions += int(metrics.get("reactions") or metrics.get("likes") or 0)
            total_comments += int(metrics.get("comments") or 0)
            total_shares += int(metrics.get("shares") or 0)
            total_clicks += int(metrics.get("clicks") or 0)

        total_engagement = total_reactions + total_comments + total_shares + total_clicks
        engagement_rate = round((total_engagement / max(total_impressions, 1)) * 100, 2)

        return {
            "period_days": days,
            "total_posts": total_posts,
            "total_impressions": total_impressions,
            "total_reach": total_reach,
            "total_engagement": total_engagement,
            "engagement_rate": engagement_rate,
            "reactions": total_reactions,
            "comments": total_comments,
            "shares": total_shares,
            "clicks": total_clicks,
            "platform_breakdown": platform_breakdown,
        }

    async def get_calendar_items(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Return posts scheduled or published within the date window for calendar display."""
        stmt = select(SocialPost).where(
            (
                and_(
                    SocialPost.status == SocialPostStatus.SCHEDULED.value,
                    SocialPost.scheduled_at >= start_date,
                    SocialPost.scheduled_at <= end_date,
                )
            )
            | (
                and_(
                    SocialPost.status == SocialPostStatus.PUBLISHED.value,
                    SocialPost.published_at >= start_date,
                    SocialPost.published_at <= end_date,
                )
            )
            | (
                and_(
                    SocialPost.status == SocialPostStatus.DRAFT.value,
                    SocialPost.created_at >= start_date,
                    SocialPost.created_at <= end_date,
                )
            )
        ).order_by(SocialPost.scheduled_at.asc(), SocialPost.created_at.asc())

        if organization_id:
            stmt = stmt.where(SocialPost.organization_id == organization_id)

        posts = (await self.session.scalars(stmt)).unique().all()
        items = []
        for post in posts:
            display_dt = post.scheduled_at or post.published_at or post.created_at
            items.append({
                "id": str(post.id),
                "title": post.title,
                "caption": post.caption[:120] + ("..." if len(post.caption) > 120 else ""),
                "status": post.status,
                "approval_status": post.approval_status,
                "platforms": [t.platform for t in post.targets],
                "date": display_dt.strftime("%Y-%m-%d") if display_dt else None,
                "time": display_dt.strftime("%H:%M") if display_dt else None,
                "iso_timestamp": display_dt.isoformat() if display_dt else None,
                "media_count": len(post.media_urls or []),
            })
        return items
