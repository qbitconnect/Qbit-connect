"""Operator UI — Social Media Management, Composer, Calendar & Analytics (Phase 8 §14).

Pages:
    GET  /social            hub: overview metrics, accounts summary, publishing queue
    GET  /social/composer   composer: multi-account targeting, platform preview, media attachments
    POST /social/composer   create/schedule/draft form submission
    GET  /social/calendar   visual content calendar grid
    GET  /social/approval   pending review queue for team leads
    GET  /social/analytics  real provider performance analytics
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import Settings
from app.core.errors import QBITError, ValidationError
from app.core.logging import get_logger
from app.models.marketing import Campaign
from app.models.social import (
    ApprovalStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialPost,
    SocialPostStatus,
    SocialPostTarget,
)
from app.services.marketing.providers import build_provider_registry
from app.services.social.analytics import SocialAnalyticsService
from app.services.social.publisher import SocialPublisher
from app.services.social.service import SocialPostService
from app.ui import _ctx, require_ui_permission, templates, ui_user_for

logger = get_logger("qbit.ui.social")

social_view = ui_user_for("social.view")
router = APIRouter(tags=["social-ui"])


def _registry(request: Request):
    registry = getattr(request.app.state, "marketing_providers", None)
    if not registry:
        registry = build_provider_registry(request.app.state.settings)
        request.app.state.marketing_providers = registry
    return registry


# --- 1. Hub / Publishing Queue ------------------------------------------------

@router.get("/social", response_class=HTMLResponse)
async def social_overview(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
    tab: str = "queue",
    status_filter: str | None = None,
):
    svc = SocialPostService(session)
    analytics_svc = SocialAnalyticsService(session, _registry(request), request.app.state.settings)

    # 1. Accounts
    accounts = (await session.scalars(select(SocialAccount).order_by(SocialAccount.created_at.desc()))).all()

    # 2. Posts (Queue)
    posts = await svc.list_posts(status=status_filter, limit=50)

    # 3. Quick metrics
    metrics = await analytics_svc.get_overview_metrics(days=30)

    ctx = _ctx(
        request, user,
        tab=tab,
        accounts=[a.to_public_dict() for a in accounts],
        posts=[p.to_dict() for p in posts],
        metrics=metrics,
        active_tab="social",
    )
    return templates.TemplateResponse(request, "social/index.html", ctx)


# --- 2. Composer --------------------------------------------------------------

@router.get("/social/composer", response_class=HTMLResponse)
async def social_composer(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
    clone_id: str | None = None,
):
    accounts = (await session.scalars(
        select(SocialAccount).where(SocialAccount.status == SocialAccountStatus.ACTIVE.value)
    )).all()
    campaigns = (await session.scalars(select(Campaign).limit(30))).all()

    clone_post = None
    if clone_id:
        try:
            clone_post = await session.get(SocialPost, uuid.UUID(clone_id))
        except Exception:
            pass

    ctx = _ctx(
        request, user,
        accounts=[a.to_public_dict() for a in accounts],
        campaigns=campaigns,
        clone_post=clone_post.to_dict() if clone_post else None,
        active_tab="composer",
    )
    return templates.TemplateResponse(request, "social/composer.html", ctx)


@router.post("/social/composer")
async def handle_composer_submit(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
    title: str = Form(...),
    caption: str = Form(...),
    action: str = Form(...),  # "draft", "submit_approval", "schedule", "publish_now"
    account_ids: list[str] = Form(...),
    scheduled_at: str | None = Form(default=None),
    post_timezone: str = Form(default="UTC"),
    campaign_id: str | None = Form(default=None),
    image_url: str | None = Form(default=None),
    video_url: str | None = Form(default=None),
):
    svc = SocialPostService(session)
    user_id = getattr(user, "id", None)

    media = []
    if image_url and image_url.strip():
        media.append({"url": image_url.strip(), "type": "IMAGE", "alt_text": "Attached Image"})
    elif video_url and video_url.strip():
        media.append({"url": video_url.strip(), "type": "VIDEO", "alt_text": "Attached Video"})

    sched_dt = None
    if scheduled_at and scheduled_at.strip():
        try:
            sched_dt = datetime.fromisoformat(scheduled_at.strip())
        except Exception:
            pass

    as_draft = action in ("draft", "submit_approval")
    camp_uuid = uuid.UUID(campaign_id) if campaign_id and campaign_id.strip() else None

    try:
        post = await svc.create_post(
            organization_id=None,
            author_id=user_id,
            title=title,
            caption=caption,
            account_ids=account_ids,
            media_urls=media,
            scheduled_at=sched_dt,
            post_timezone=post_timezone,
            campaign_id=camp_uuid,
            as_draft=as_draft,
        )

        if action == "submit_approval":
            await svc.submit_for_approval(post.id, actor_id=user_id)
        elif action == "publish_now":
            publisher = SocialPublisher(session, _registry(request), request.app.state.settings)
            await publisher.publish_post_now(post.id)

        return RedirectResponse("/social?ok=1", status_code=303)
    except Exception as exc:
        logger.exception("Composer submission error")
        return RedirectResponse(f"/social/composer?error={str(exc)}", status_code=303)


# --- 3. Calendar --------------------------------------------------------------

@router.get("/social/calendar", response_class=HTMLResponse)
async def social_calendar(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=15)
    end = now + timedelta(days=45)

    svc = SocialAnalyticsService(session, _registry(request), request.app.state.settings)
    items = await svc.get_calendar_items(start_date=start, end_date=end)

    ctx = _ctx(
        request, user,
        calendar_items=items,
        active_tab="calendar",
    )
    return templates.TemplateResponse(request, "social/calendar.html", ctx)


# --- 4. Approval Queue --------------------------------------------------------

@router.get("/social/approval", response_class=HTMLResponse)
async def social_approval_queue(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
):
    stmt = (
        select(SocialPost)
        .where(SocialPost.status == SocialPostStatus.PENDING_APPROVAL.value)
        .order_by(desc(SocialPost.created_at))
    )
    posts = (await session.scalars(stmt)).unique().all()

    ctx = _ctx(
        request, user,
        pending_posts=[p.to_dict() for p in posts],
        active_tab="approval",
    )
    return templates.TemplateResponse(request, "social/approval.html", ctx)


@router.post("/social/posts/{id}/approve")
async def approve_post_action(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
):
    svc = SocialPostService(session)
    await svc.approve_post(id, approver_id=getattr(user, "id", None))
    return RedirectResponse("/social/approval?approved=1", status_code=303)


@router.post("/social/posts/{id}/reject")
async def reject_post_action(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
    reason: str = Form(default="Content changes required"),
):
    svc = SocialPostService(session)
    await svc.reject_post(id, approver_id=getattr(user, "id", None), reason=reason)
    return RedirectResponse("/social/approval?rejected=1", status_code=303)


# --- 5. Analytics -------------------------------------------------------------

@router.get("/social/analytics", response_class=HTMLResponse)
async def social_analytics(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[object, Depends(social_view)],
    days: int = 30,
):
    svc = SocialAnalyticsService(session, _registry(request), request.app.state.settings)
    overview = await svc.get_overview_metrics(days=days)

    stmt = (
        select(SocialPostTarget)
        .where(SocialPostTarget.status == "PUBLISHED")
        .order_by(desc(SocialPostTarget.published_at))
        .limit(25)
    )
    targets = (await session.scalars(stmt)).all()

    ctx = _ctx(
        request, user,
        metrics=overview,
        recent_published=[t.to_dict() for t in targets],
        days=days,
        active_tab="analytics",
    )
    return templates.TemplateResponse(request, "social/analytics.html", ctx)
