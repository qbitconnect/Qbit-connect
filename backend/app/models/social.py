"""Social Media Integration & Publishing models (Phase 8).

Tables:
- SocialAccount      Authorized platform account (Page, IG Professional, LinkedIn Org/Profile)
- SocialPost         Master social post / draft / scheduled publication
- SocialPostTarget   Per-account platform dispatch target with container/post id, status, retries & analytics
- SocialAuditLog     Immutable event log for approvals, scheduling, publishing, and errors
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
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


class SocialPlatform(str, enum.Enum):
    FACEBOOK = "FACEBOOK"
    INSTAGRAM = "INSTAGRAM"
    LINKEDIN = "LINKEDIN"
    TWITTER = "TWITTER"


class SocialAccountType(str, enum.Enum):
    PAGE = "PAGE"
    BUSINESS = "BUSINESS"
    ORGANIZATION = "ORGANIZATION"
    PROFILE = "PROFILE"


class SocialAccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    FAILED = "FAILED"


class SocialPostStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TargetStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SocialAccount(Base):
    """Authorized social media account / Page / LinkedIn company profile."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        Index("ix_social_accounts_org", "organization_id"),
        Index("ix_social_accounts_platform", "platform"),
        Index("ix_social_accounts_status", "status"),
        UniqueConstraint("platform", "account_id", "organization_id", name="uq_social_platform_account"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PAGE")
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)  # external platform ID
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default=SocialAccountStatus.ACTIVE.value)
    status_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Encrypted credential reference / payload (never serialized to public dict)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text(), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scopes: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    targets: Mapped[list["SocialPostTarget"]] = relationship("SocialPostTarget", back_populates="social_account", cascade="all, delete-orphan", lazy="selectin")

    def to_public_dict(self) -> dict[str, Any]:
        """Display-safe representation: zero credentials or secrets exposed."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "platform": self.platform,
            "account_type": self.account_type,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "username": self.username,
            "profile_picture_url": self.profile_picture_url,
            "status": self.status,
            "status_message": self.status_message,
            "token_expires_at": self.token_expires_at.isoformat() if self.token_expires_at else None,
            "scopes": self.scopes or [],
            "metadata": {
                k: v for k, v in (self.metadata_json or {}).items()
                if not any(s in k.lower() for s in ("token", "secret", "key", "password"))
            },
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SocialPost(Base):
    """Canonical post authored in Qbit Connect composer."""

    __tablename__ = "social_posts"
    __table_args__ = (
        Index("ix_social_posts_org", "organization_id"),
        Index("ix_social_posts_status", "status"),
        Index("ix_social_posts_scheduled", "scheduled_at"),
        Index("ix_social_posts_campaign", "campaign_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    caption: Mapped[str] = mapped_column(Text(), nullable=False)

    #: Per-platform overrides (e.g. {"INSTAGRAM": {"caption": "... #hashtags"}, "LINKEDIN": {...}})
    platform_customizations: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)

    #: List of media descriptors: [{"url": "...", "type": "IMAGE"|"VIDEO", "alt_text": "...", "thumbnail_url": "..."}]
    media_urls: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default=SocialPostStatus.DRAFT.value)
    approval_status: Mapped[str] = mapped_column(String(50), nullable=False, default=ApprovalStatus.DRAFT.value)
    approver_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    targets: Mapped[list["SocialPostTarget"]] = relationship("SocialPostTarget", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    audit_logs: Mapped[list["SocialAuditLog"]] = relationship("SocialAuditLog", back_populates="post", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "author_id": str(self.author_id) if self.author_id else None,
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "title": self.title,
            "caption": self.caption,
            "platform_customizations": self.platform_customizations or {},
            "media_urls": self.media_urls or [],
            "status": self.status,
            "approval_status": self.approval_status,
            "approver_id": str(self.approver_id) if self.approver_id else None,
            "rejection_reason": self.rejection_reason,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "timezone": self.timezone,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "targets": [t.to_dict() for t in (self.targets or [])],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SocialPostTarget(Base):
    """Specific dispatch target for a post to a social account."""

    __tablename__ = "social_post_targets"
    __table_args__ = (
        Index("ix_social_post_targets_post", "post_id"),
        Index("ix_social_post_targets_account", "social_account_id"),
        Index("ix_social_post_targets_status", "status"),
        UniqueConstraint("post_id", "social_account_id", name="uq_post_target_account"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False)
    social_account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False)

    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=TargetStatus.PENDING.value)

    provider_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_post_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider_container_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # for async upload containers

    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, default=lambda: uuid.uuid4().hex)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analytics: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    analytics_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = timestamp_columns()[0]
    updated_at: Mapped[datetime] = timestamp_columns()[1]

    post: Mapped["SocialPost"] = relationship("SocialPost", back_populates="targets", lazy="selectin")
    social_account: Mapped["SocialAccount"] = relationship("SocialAccount", back_populates="targets", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "post_id": str(self.post_id),
            "social_account_id": str(self.social_account_id),
            "platform": self.platform,
            "status": self.status,
            "provider_post_id": self.provider_post_id,
            "provider_post_url": self.provider_post_url,
            "provider_container_id": self.provider_container_id,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "retry_count": self.retry_count,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "analytics": self.analytics or {},
            "analytics_updated_at": self.analytics_updated_at.isoformat() if self.analytics_updated_at else None,
            "account_name": self.social_account.account_name if self.social_account else None,
            "username": self.social_account.username if self.social_account else None,
        }


class SocialAuditLog(Base):
    """Immutable audit trail for all post modifications, approvals, and dispatches."""

    __tablename__ = "social_audit_logs"
    __table_args__ = (
        Index("ix_social_audit_post", "post_id"),
        Index("ix_social_audit_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    post: Mapped["SocialPost"] = relationship("SocialPost", back_populates="audit_logs", lazy="selectin")
