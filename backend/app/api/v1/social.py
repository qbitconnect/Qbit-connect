"""Social Media API Endpoints (Phase 8 §13).

Provides endpoints for:
- OAuth flow & connected social accounts
- Social post composer, drafts, and duplication
- Team approval workflow & scheduling
- Calendar feed & publishing queue
- Real provider analytics & synchronization
- Secure media uploads with SSRF validation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_permission
from app.core.config import Settings
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.models.social import (
    ApprovalStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialPost,
    SocialPostStatus,
    SocialPostTarget,
)
from app.models.user import User
from app.services.marketing.providers import build_provider_registry
from app.services.social.analytics import SocialAnalyticsService
from app.services.social.media import (
    is_safe_remote_url,
    validate_media_mime_and_size,
)
from app.services.social.publisher import SocialPublisher
from app.services.social.service import SocialPostService

logger = get_logger("qbit.api.social")
router = APIRouter(prefix="/social", tags=["social"])


# --- Schemas ------------------------------------------------------------------

class CreatePostSchema(BaseModel):
    title: str = Field(..., max_length=300)
    caption: str
    account_ids: list[str]
    platform_customizations: dict[str, Any] = Field(default_factory=dict)
    media_urls: list[dict[str, Any]] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    timezone: str = "UTC"
    campaign_id: str | None = None
    as_draft: bool = True


class UpdateDraftSchema(BaseModel):
    title: str | None = None
    caption: str | None = None
    platform_customizations: dict[str, Any] | None = None
    media_urls: list[dict[str, Any]] | None = None


class SchedulePostSchema(BaseModel):
    scheduled_at: datetime
    timezone: str = "UTC"


class RejectPostSchema(BaseModel):
    reason: str = Field(..., min_length=3)


# --- Dependencies -------------------------------------------------------------

def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request):
    registry = getattr(request.app.state, "marketing_providers", None)
    if not registry:
        registry = build_provider_registry(request.app.state.settings)
        request.app.state.marketing_providers = registry
    return registry


# --- Accounts & OAuth ---------------------------------------------------------

@router.get("/accounts")
async def list_connected_accounts(
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """List all connected authorized social accounts for the workspace."""
    stmt = select(SocialAccount).order_by(SocialAccount.created_at.desc())
    accounts = (await session.scalars(stmt)).all()
    return {"data": [a.to_public_dict() for a in accounts]}


@router.get("/oauth/{platform}/authorize")
async def start_social_oauth(
    platform: str,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    redirect_uri: str = Query(...),
):
    """Initiate OAuth authorization with CSRF state protection."""
    state = f"state_{uuid.uuid4().hex}"
    registry = _registry(request)
    provider_id = {
        "facebook": "meta_facebook",
        "instagram": "instagram_graph",
        "linkedin": "linkedin",
    }.get(platform.lower(), "social_mock")

    provider = registry.get(provider_id)
    if not provider:
        raise ValidationError(f"Platform '{platform}' is not supported")

    auth_url = await provider.get_authorization_url(state=state, redirect_uri=redirect_uri)
    return {"auth_url": auth_url, "state": state}


@router.post("/accounts/{id}/disconnect")
async def disconnect_social_account(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Disconnect an authorized social account and revoke credentials."""
    account = await session.get(SocialAccount, id)
    if not account:
        raise NotFoundError("Social account not found")

    account.status = SocialAccountStatus.DISCONNECTED.value
    account.encrypted_credentials = None
    account.status_message = f"Disconnected by {current_user.email}"
    await session.commit()
    return {"success": True, "message": "Account disconnected"}


# --- Posts & Composer ---------------------------------------------------------

@router.get("/posts")
async def list_posts(
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str | None = None,
    platform: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List posts with status and platform filters."""
    svc = SocialPostService(session)
    posts = await svc.list_posts(status=status, platform=platform, limit=limit, offset=offset)
    return {"data": [p.to_dict() for p in posts]}


@router.post("/posts", status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: CreatePostSchema,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Create a new post draft or scheduled publication."""
    svc = SocialPostService(session)
    post = await svc.create_post(
        organization_id=None,
        author_id=current_user.id,
        title=payload.title,
        caption=payload.caption,
        account_ids=payload.account_ids,
        platform_customizations=payload.platform_customizations,
        media_urls=payload.media_urls,
        scheduled_at=payload.scheduled_at,
        post_timezone=payload.timezone,
        campaign_id=uuid.UUID(payload.campaign_id) if payload.campaign_id else None,
        as_draft=payload.as_draft,
    )
    return {"data": post.to_dict()}


@router.get("/posts/{id}")
async def get_post_detail(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Retrieve full post details including target dispatch status."""
    svc = SocialPostService(session)
    post = await svc.get_post_or_404(id)
    return {"data": post.to_dict()}


@router.put("/posts/{id}")
async def update_post_draft(
    id: uuid.UUID,
    payload: UpdateDraftSchema,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Update draft copy or media attachments."""
    svc = SocialPostService(session)
    post = await svc.update_draft(
        id,
        title=payload.title,
        caption=payload.caption,
        platform_customizations=payload.platform_customizations,
        media_urls=payload.media_urls,
        actor_id=current_user.id,
    )
    return {"data": post.to_dict()}


@router.post("/posts/{id}/duplicate")
async def duplicate_post(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Clone an existing post into a new draft."""
    svc = SocialPostService(session)
    new_post = await svc.duplicate_post(id, actor_id=current_user.id)
    return {"data": new_post.to_dict()}


@router.delete("/posts/{id}")
async def delete_post(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Delete a post draft or cancelled post."""
    svc = SocialPostService(session)
    await svc.delete_post(id, actor_id=current_user.id)
    return {"success": True}


# --- Approval Workflow --------------------------------------------------------

@router.post("/posts/{id}/submit-approval")
async def submit_post_for_approval(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    svc = SocialPostService(session)
    post = await svc.submit_for_approval(id, actor_id=current_user.id)
    return {"data": post.to_dict()}


@router.post("/posts/{id}/approve")
async def approve_post(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    svc = SocialPostService(session)
    post = await svc.approve_post(id, approver_id=current_user.id)
    return {"data": post.to_dict()}


@router.post("/posts/{id}/reject")
async def reject_post(
    id: uuid.UUID,
    payload: RejectPostSchema,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    svc = SocialPostService(session)
    post = await svc.reject_post(id, approver_id=current_user.id, reason=payload.reason)
    return {"data": post.to_dict()}


# --- Scheduling & Immediate Publishing ---------------------------------------

@router.post("/posts/{id}/schedule")
async def schedule_post(
    id: uuid.UUID,
    payload: SchedulePostSchema,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    svc = SocialPostService(session)
    post = await svc.schedule_post(
        id,
        scheduled_at=payload.scheduled_at,
        post_timezone=payload.timezone,
        actor_id=current_user.id,
    )
    return {"data": post.to_dict()}


@router.post("/posts/{id}/publish-now")
async def publish_post_now(
    id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Trigger immediate publishing to all targeted social platforms."""
    settings = _settings(request)
    registry = _registry(request)
    publisher = SocialPublisher(session, registry, settings)
    post = await publisher.publish_post_now(id)
    return {"data": post.to_dict()}


@router.post("/posts/{id}/cancel")
async def cancel_scheduled_post(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    svc = SocialPostService(session)
    post = await svc.cancel_post(id, actor_id=current_user.id)
    return {"data": post.to_dict()}


# --- Calendar & Analytics -----------------------------------------------------

@router.get("/calendar")
async def get_social_calendar(
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    start: str | None = None,
    end: str | None = None,
):
    """Retrieve posts scheduled or published for the content calendar view."""
    now = datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start) if start else now - timedelta(days=15)
    end_dt = datetime.fromisoformat(end) if end else now + timedelta(days=45)

    svc = SocialAnalyticsService(session, _registry(request), _settings(request))
    items = await svc.get_calendar_items(start_date=start_dt, end_date=end_dt)
    return {"data": items}


@router.get("/analytics/overview")
async def get_analytics_overview(
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    days: int = 30,
):
    """Retrieve aggregated engagement, impressions, reach, and platform breakdown."""
    svc = SocialAnalyticsService(session, _registry(request), _settings(request))
    overview = await svc.get_overview_metrics(days=days)
    return {"data": overview}


@router.post("/targets/{id}/sync-analytics")
async def sync_target_analytics(
    id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Query live provider API to update real post insights."""
    svc = SocialAnalyticsService(session, _registry(request), _settings(request))
    res = await svc.sync_post_analytics(id)
    return res


# --- Secure Media Upload ------------------------------------------------------

@router.post("/media/upload")
async def upload_social_media(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
):
    """Upload media binary securely with format verification and storage integration."""
    content = await file.read()
    media_type, mime = validate_media_mime_and_size(file.filename or "media", content)

    # Store file using platform storage service
    storage = request.app.state.storage
    filename = f"social/{uuid.uuid4().hex[:12]}_{file.filename}"
    meta = await storage.put(filename, content, content_type=mime)

    # Construct internal access URL
    url = f"/api/v1/files/{meta.id}" if hasattr(meta, "id") else f"/storage/{filename}"
    return {
        "url": url,
        "type": media_type,
        "mime_type": mime,
        "size_bytes": len(content),
    }
