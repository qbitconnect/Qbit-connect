"""Deterministic Mock Provider for Social Media (Phase 8 test suite).

Gated strictly to test environments — provides zero-network simulation of
Facebook, Instagram, and LinkedIn OAuth, publishing, and analytics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.marketing.providers.base import ErrorClass
from app.services.marketing.providers.social.base import (
    BaseSocialProvider,
    SocialAnalyticsResult,
    SocialPublishResult,
)


class SocialMockProvider(BaseSocialProvider):
    provider_id: str = "social_mock"
    platform: str = "MOCK"
    test_only: bool = True

    def __init__(self, platform_name: str = "MOCK") -> None:
        self.platform = platform_name
        self.published_posts: list[dict[str, Any]] = []

    async def validate_configuration(self, config: dict) -> list[str]:
        return []

    async def validate_recipient(self, address: str) -> bool:
        return True

    # --- OAuth & Discovery ---------------------------------------------------

    async def get_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
    ) -> str:
        return f"https://mock-oauth.qbit.test/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        return {
            "access_token": f"mock_token_{uuid.uuid4().hex[:12]}",
            "token_type": "bearer",
            "expires_in": 5184000,
        }

    async def refresh_or_validate_token(
        self,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        token = credentials.get("access_token")
        if token and not token.startswith("invalid_"):
            return {"valid": True, "user_id": "mock_user_123"}
        return {"valid": False, "error": "Token is invalid or expired"}

    async def get_connected_accounts(
        self,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "platform": "FACEBOOK",
                "account_type": "PAGE",
                "account_id": "fb_page_1001",
                "account_name": "QbitPro Technologies Page",
                "username": "@qbitpro",
                "access_token": "mock_page_token_fb",
            },
            {
                "platform": "INSTAGRAM",
                "account_type": "BUSINESS",
                "account_id": "ig_biz_2001",
                "account_name": "QbitPro Official Instagram",
                "username": "@qbitpro_tech",
                "access_token": "mock_ig_token",
            },
            {
                "platform": "LINKEDIN",
                "account_type": "ORGANIZATION",
                "account_id": "urn:li:organization:3001",
                "account_name": "QbitPro India LinkedIn",
                "username": None,
                "access_token": "mock_li_token",
            },
        ]

    async def get_account_permissions(
        self,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        return {"can_post": True, "tasks": ["CREATE_CONTENT", "MANAGE"]}

    # --- Publishing -----------------------------------------------------------

    async def publish_text_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        if "fail" in text.lower():
            return SocialPublishResult.failure("Simulated provider failure", code="SIMULATED_FAIL")

        post_id = f"post_mock_{uuid.uuid4().hex[:10]}"
        result = SocialPublishResult.success(
            post_id,
            post_url=f"https://social.test/posts/{post_id}",
            metadata={"idempotency_key": idempotency_key},
        )
        self.published_posts.append({"id": post_id, "text": text, "config": account_config})
        return result

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
        post_id = f"post_img_{uuid.uuid4().hex[:10]}"
        result = SocialPublishResult.success(
            post_id,
            post_url=f"https://social.test/posts/{post_id}",
            metadata={"media_url": media_url, "alt_text": alt_text},
        )
        self.published_posts.append({"id": post_id, "text": text, "media_url": media_url})
        return result

    async def publish_video_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        video_url: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        post_id = f"post_vid_{uuid.uuid4().hex[:10]}"
        return SocialPublishResult.success(
            post_id,
            post_url=f"https://social.test/reels/{post_id}",
            metadata={"video_url": video_url},
        )

    async def publish_carousel_or_multi_media_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        media_items: list[dict[str, Any]],
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        post_id = f"post_car_{uuid.uuid4().hex[:10]}"
        return SocialPublishResult.success(
            post_id,
            post_url=f"https://social.test/carousel/{post_id}",
            metadata={"items_count": len(media_items)},
        )

    async def get_post_status(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        return {"status": "PUBLISHED", "provider_post_id": provider_post_id}

    async def get_post_analytics(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> SocialAnalyticsResult:
        now = datetime.now(timezone.utc)
        return SocialAnalyticsResult(
            provider_post_id=provider_post_id,
            platform=self.platform,
            retrieved_at=now,
            metrics={
                "impressions": 1250,
                "reach": 980,
                "reactions": 85,
                "likes": 72,
                "comments": 14,
                "shares": 9,
                "clicks": 34,
            },
            available=True,
        )
