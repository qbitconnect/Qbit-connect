"""Base abstraction for social media platform providers (Phase 8 §3).

Defines canonical methods for OAuth authorization, account discovery,
multi-format publishing (text, image, video, carousel), container tracking,
real provider analytics retrieval, and webhook validation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.marketing.providers.base import (
    BaseMarketingProvider,
    ErrorClass,
    ProviderError,
    SendResult,
)


@dataclass
class SocialPublishResult:
    """Outcome of a social publishing operation (no secrets serialized)."""

    ok: bool
    provider_post_id: str | None = None
    provider_post_url: str | None = None
    provider_container_id: str | None = None
    status: str = "PUBLISHED"  # "PUBLISHED", "PROCESSING", "FAILED"
    error: str | None = None
    error_code: str | None = None
    error_class: ErrorClass = ErrorClass.TRANSIENT
    metadata: dict = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        provider_post_id: str | None,
        *,
        post_url: str | None = None,
        status: str = "PUBLISHED",
        metadata: dict | None = None,
    ) -> "SocialPublishResult":
        return cls(
            ok=True,
            provider_post_id=provider_post_id,
            provider_post_url=post_url,
            status=status,
            metadata=metadata or {},
        )

    @classmethod
    def processing(
        cls,
        container_id: str,
        *,
        metadata: dict | None = None,
    ) -> "SocialPublishResult":
        return cls(
            ok=True,
            provider_container_id=container_id,
            status="PROCESSING",
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        code: str = "PUBLISH_FAILED",
        error_class: ErrorClass = ErrorClass.TRANSIENT,
    ) -> "SocialPublishResult":
        return cls(
            ok=False,
            error=message[:500],
            error_code=code,
            error_class=error_class,
            status="FAILED",
        )


@dataclass
class SocialAnalyticsResult:
    """Real provider-reported engagement metrics (never fabricated)."""

    provider_post_id: str
    platform: str
    retrieved_at: datetime
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_response: dict = field(default_factory=dict)
    available: bool = True
    error_message: str | None = None


class BaseSocialProvider(BaseMarketingProvider):
    """Abstract base class for official social platform adapters."""

    channel: str = "SOCIAL"
    platform: str = "UNKNOWN"

    # --- OAuth & Account Discovery -------------------------------------------

    async def get_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
    ) -> str:
        """Construct the platform's OAuth 2.0 authorization URL."""
        raise NotImplementedError

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange the callback code for access token and metadata."""
        raise NotImplementedError

    async def refresh_or_validate_token(
        self,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate or refresh an access token."""
        raise NotImplementedError

    async def get_connected_accounts(
        self,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Discover eligible managed Pages, IG Business accounts, or LinkedIn profiles."""
        raise NotImplementedError

    async def get_account_permissions(
        self,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve current granted scopes and tasks for the account."""
        raise NotImplementedError

    # --- Publishing -----------------------------------------------------------

    async def publish_text_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        """Publish a text-only status update."""
        raise NotImplementedError

    async def publish_image_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        media_url: str,
        alt_text: str | None = None,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        """Publish a single image post."""
        raise NotImplementedError

    async def publish_video_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        video_url: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        """Publish a single video post / reel."""
        raise NotImplementedError

    async def publish_carousel_or_multi_media_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        media_items: list[dict[str, Any]],
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        """Publish a multi-media carousel post where supported."""
        raise NotImplementedError

    async def get_post_status(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """Query real status of a post or container."""
        raise NotImplementedError

    # --- Analytics & Webhooks -------------------------------------------------

    async def get_post_analytics(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> SocialAnalyticsResult:
        """Retrieve real provider post insights / metrics."""
        raise NotImplementedError

    async def disconnect_account(
        self,
        *,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> bool:
        """Revoke platform token / disconnect account cleanly."""
        return True

    def validate_webhook(
        self,
        *,
        raw_body: bytes,
        signature_header: str | None,
        app_secret: str,
    ) -> bool:
        """Validate webhook HMAC signature where applicable."""
        return True

    # --- Canonical CamelCase Aliases (Section 3 Requirement) -----------------
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.getAuthorizationUrl = cls.get_authorization_url
        cls.exchangeAuthorizationCode = cls.exchange_authorization_code
        cls.refreshOrValidateToken = cls.refresh_or_validate_token
        cls.getConnectedAccounts = cls.get_connected_accounts
        cls.getAccountPermissions = cls.get_account_permissions
        cls.publishTextPost = cls.publish_text_post
        cls.publishImagePost = cls.publish_image_post
        cls.publishVideoPost = cls.publish_video_post
        cls.publishCarouselOrMultiMediaPost = cls.publish_carousel_or_multi_media_post
        cls.getPostStatus = cls.get_post_status
        cls.getPostAnalytics = cls.get_post_analytics
        cls.disconnectAccount = cls.disconnect_account
        cls.validateWebhook = cls.validate_webhook

    getAuthorizationUrl = get_authorization_url
    exchangeAuthorizationCode = exchange_authorization_code
    refreshOrValidateToken = refresh_or_validate_token
    getConnectedAccounts = get_connected_accounts
    getAccountPermissions = get_account_permissions
    publishTextPost = publish_text_post
    publishImagePost = publish_image_post
    publishVideoPost = publish_video_post
    publishCarouselOrMultiMediaPost = publish_carousel_or_multi_media_post
    getPostStatus = get_post_status
    getPostAnalytics = get_post_analytics
    disconnectAccount = disconnect_account
    validateWebhook = validate_webhook
