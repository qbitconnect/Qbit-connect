"""Official Meta Instagram Graph API provider adapter (Phase 8 §3).

Supports Instagram Professional / Business accounts eligible for the
Graph API Publishing lifecycle:
  1. Create Media Container (Image, Reel/Video, or Carousel)
  2. Check Container Readiness / Status
  3. Publish Container (`media_publish`)
  4. Fetch real post analytics (Impressions, Reach, Engagement, Saved)
"""

from __future__ import annotations

import asyncio
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.marketing.providers.base import ErrorClass
from app.services.marketing.providers.social.base import (
    BaseSocialProvider,
    SocialAnalyticsResult,
    SocialPublishResult,
)

logger = get_logger("qbit.social.instagram")

DEFAULT_GRAPH_VERSION = "v20.0"
GRAPH_BASE_URL = "https://graph.facebook.com"


class InstagramGraphProvider(BaseSocialProvider):
    provider_id: str = "instagram_graph"
    platform: str = "INSTAGRAM"

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_version: str = DEFAULT_GRAPH_VERSION,
        base_url: str = GRAPH_BASE_URL,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_version = api_version
        self.base_url = f"{base_url}/{api_version}"

    async def validate_configuration(self, config: dict) -> list[str]:
        problems = []
        if not config.get("instagram_business_account_id"):
            problems.append("instagram_business_account_id is required")
        return problems

    async def validate_recipient(self, address: str) -> bool:
        return bool(address and address.strip())

    # --- OAuth & Discovery ---------------------------------------------------

    async def get_authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
    ) -> str:
        app_id = self.app_id or "APP_ID_NOT_CONFIGURED"
        default_scopes = [
            "instagram_basic",
            "instagram_content_publish",
            "pages_show_list",
            "pages_read_engagement",
            "business_management",
        ]
        chosen_scopes = scopes or default_scopes
        params = {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(chosen_scopes),
            "response_type": "code",
        }
        return f"https://www.facebook.com/{self.api_version}/dialog/oauth?{urllib.parse.urlencode(params)}"

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        if not self.app_id or not self.app_secret:
            raise ValueError("Meta App ID and App Secret are required for Instagram OAuth")

        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.base_url}/oauth/access_token", params=params)
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text)
                raise RuntimeError(f"Instagram token exchange failed: {err}")
            return resp.json()

    async def refresh_or_validate_token(
        self,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        token = credentials.get("access_token")
        if not token:
            return {"valid": False, "error": "Missing access token"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/me", params={"access_token": token})
            if resp.status_code == 200:
                return {"valid": True, "data": resp.json()}
            err = resp.json().get("error", {}).get("message", "Token expired or invalid")
            return {"valid": False, "error": err}

    async def get_connected_accounts(
        self,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Find Instagram Business accounts connected to user's managed Facebook Pages."""
        user_token = credentials.get("access_token")
        if not user_token:
            return []
        async with httpx.AsyncClient(timeout=30.0) as client:
            # First fetch user pages with instagram_business_account field
            resp = await client.get(
                f"{self.base_url}/me/accounts",
                params={
                    "access_token": user_token,
                    "fields": "id,name,access_token,instagram_business_account{id,username,name,profile_picture_url}",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Failed to discover connected Instagram accounts: {resp.text}")
                return []
            data = resp.json().get("data", [])
            accounts = []
            for page in data:
                ig_account = page.get("instagram_business_account")
                if ig_account and ig_account.get("id"):
                    accounts.append({
                        "platform": "INSTAGRAM",
                        "account_type": "BUSINESS",
                        "account_id": str(ig_account.get("id")),
                        "account_name": ig_account.get("name") or ig_account.get("username") or page.get("name"),
                        "username": f"@{ig_account.get('username')}" if ig_account.get("username") else None,
                        "profile_picture_url": ig_account.get("profile_picture_url"),
                        "access_token": page.get("access_token") or user_token,
                        "linked_page_id": page.get("id"),
                    })
            return accounts

    async def get_account_permissions(
        self,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        ig_id = account_config.get("instagram_business_account_id")
        token = credentials.get("access_token")
        if not ig_id or not token:
            return {"eligible": False, "error": "Missing Instagram Account ID or token"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/{ig_id}",
                params={"access_token": token, "fields": "id,username"},
            )
            if resp.status_code == 200:
                return {"eligible": True, "data": resp.json()}
            return {"eligible": False, "error": resp.text}

    # --- Publishing Lifecycle (Container -> Check -> Publish) ----------------

    async def publish_text_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        # Instagram Business API does not support text-only posts without media
        return SocialPublishResult.failure(
            "Instagram Business API requires an image, video, or carousel. Text-only posts are unsupported on Instagram.",
            code="IG_TEXT_ONLY_UNSUPPORTED",
            error_class=ErrorClass.PERMANENT,
        )

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
        ig_id = account_config.get("instagram_business_account_id")
        token = credentials.get("access_token")
        if not ig_id or not token:
            return SocialPublishResult.failure(
                "Missing instagram_business_account_id or access_token",
                error_class=ErrorClass.CONFIGURATION,
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Create Media Container
            container_resp = await client.post(
                f"{self.base_url}/{ig_id}/media",
                data={
                    "image_url": media_url,
                    "caption": text,
                    "access_token": token,
                },
            )
            if container_resp.status_code not in (200, 201):
                err = container_resp.json().get("error", {}).get("message", container_resp.text)
                return SocialPublishResult.failure(f"Instagram container creation failed: {err}")

            container_id = container_resp.json().get("id")
            if not container_id:
                return SocialPublishResult.failure("Instagram returned no container id")

            # 2. Poll container status until FINISHED
            ready = await self._wait_for_container_ready(client, container_id, token)
            if not ready:
                return SocialPublishResult.failure("Instagram container failed or timed out during processing")

            # 3. Publish Media Container
            pub_resp = await client.post(
                f"{self.base_url}/{ig_id}/media_publish",
                data={"creation_id": container_id, "access_token": token},
            )
            if pub_resp.status_code in (200, 201):
                post_id = pub_resp.json().get("id")
                permalink = await self._get_permalink(client, post_id, token)
                return SocialPublishResult.success(post_id, post_url=permalink, metadata={"container_id": container_id})

            err = pub_resp.json().get("error", {}).get("message", pub_resp.text)
            return SocialPublishResult.failure(f"Instagram media_publish failed: {err}")

    async def publish_video_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        video_url: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        ig_id = account_config.get("instagram_business_account_id")
        token = credentials.get("access_token")
        if not ig_id or not token:
            return SocialPublishResult.failure(
                "Missing instagram_business_account_id or access_token",
                error_class=ErrorClass.CONFIGURATION,
            )

        async with httpx.AsyncClient(timeout=90.0) as client:
            # 1. Create Reels/Video Container
            container_resp = await client.post(
                f"{self.base_url}/{ig_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": text,
                    "access_token": token,
                },
            )
            if container_resp.status_code not in (200, 201):
                err = container_resp.json().get("error", {}).get("message", container_resp.text)
                return SocialPublishResult.failure(f"Instagram video container creation failed: {err}")

            container_id = container_resp.json().get("id")
            ready = await self._wait_for_container_ready(client, container_id, token, max_attempts=15)
            if not ready:
                return SocialPublishResult.failure("Instagram video processing timed out or failed")

            # 2. Publish
            pub_resp = await client.post(
                f"{self.base_url}/{ig_id}/media_publish",
                data={"creation_id": container_id, "access_token": token},
            )
            if pub_resp.status_code in (200, 201):
                post_id = pub_resp.json().get("id")
                permalink = await self._get_permalink(client, post_id, token)
                return SocialPublishResult.success(post_id, post_url=permalink, metadata={"container_id": container_id})

            err = pub_resp.json().get("error", {}).get("message", pub_resp.text)
            return SocialPublishResult.failure(f"Instagram video publish failed: {err}")

    async def publish_carousel_or_multi_media_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        media_items: list[dict[str, Any]],
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        ig_id = account_config.get("instagram_business_account_id")
        token = credentials.get("access_token")
        if not ig_id or not token:
            return SocialPublishResult.failure("Missing Instagram credentials", error_class=ErrorClass.CONFIGURATION)

        if not media_items or len(media_items) < 2:
            if media_items and len(media_items) == 1:
                return await self.publish_image_post(
                    account_config=account_config,
                    text=text,
                    media_url=media_items[0].get("url", ""),
                    credentials=credentials,
                    idempotency_key=idempotency_key,
                )
            return SocialPublishResult.failure("Instagram carousel requires 2 to 10 media items")

        async with httpx.AsyncClient(timeout=90.0) as client:
            child_ids = []
            for item in media_items[:10]:
                is_video = item.get("type", "").upper() == "VIDEO"
                payload = {
                    "is_carousel_item": "true",
                    "access_token": token,
                }
                if is_video:
                    payload["media_type"] = "VIDEO"
                    payload["video_url"] = item.get("url")
                else:
                    payload["image_url"] = item.get("url")

                c_resp = await client.post(f"{self.base_url}/{ig_id}/media", data=payload)
                if c_resp.status_code in (200, 201):
                    cid = c_resp.json().get("id")
                    if cid:
                        child_ids.append(cid)
                else:
                    logger.warning(f"Failed to create carousel child item: {c_resp.text}")

            if len(child_ids) < 2:
                return SocialPublishResult.failure("Failed to create enough child items for Instagram carousel")

            # Create Carousel parent container
            parent_resp = await client.post(
                f"{self.base_url}/{ig_id}/media",
                data={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": text,
                    "access_token": token,
                },
            )
            if parent_resp.status_code not in (200, 201):
                err = parent_resp.json().get("error", {}).get("message", parent_resp.text)
                return SocialPublishResult.failure(f"Instagram carousel parent creation failed: {err}")

            parent_id = parent_resp.json().get("id")
            ready = await self._wait_for_container_ready(client, parent_id, token)
            if not ready:
                return SocialPublishResult.failure("Instagram carousel processing failed")

            pub_resp = await client.post(
                f"{self.base_url}/{ig_id}/media_publish",
                data={"creation_id": parent_id, "access_token": token},
            )
            if pub_resp.status_code in (200, 201):
                post_id = pub_resp.json().get("id")
                permalink = await self._get_permalink(client, post_id, token)
                return SocialPublishResult.success(post_id, post_url=permalink)

            return SocialPublishResult.failure(f"Instagram carousel publish failed: {pub_resp.text}")

    async def _wait_for_container_ready(
        self, client: httpx.AsyncClient, container_id: str, token: str, max_attempts: int = 10
    ) -> bool:
        for _ in range(max_attempts):
            resp = await client.get(
                f"{self.base_url}/{container_id}",
                params={"access_token": token, "fields": "status_code"},
            )
            if resp.status_code == 200:
                status = resp.json().get("status_code")
                if status == "FINISHED":
                    return True
                if status in ("ERROR", "EXPIRED"):
                    return False
            await asyncio.sleep(1.5)
        return False

    async def _get_permalink(self, client: httpx.AsyncClient, post_id: str | None, token: str) -> str | None:
        if not post_id:
            return None
        try:
            resp = await client.get(f"{self.base_url}/{post_id}", params={"access_token": token, "fields": "permalink"})
            if resp.status_code == 200:
                return resp.json().get("permalink")
        except Exception:
            pass
        return None

    async def get_post_status(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        token = credentials.get("access_token")
        if not token:
            return {"status": "UNKNOWN", "error": "Missing token"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/{provider_post_id}",
                params={"access_token": token, "fields": "id,media_type,timestamp,permalink"},
            )
            if resp.status_code == 200:
                return {"status": "PUBLISHED", "data": resp.json()}
            return {"status": "FAILED", "error": resp.text}

    async def get_post_analytics(
        self,
        *,
        account_config: dict[str, Any],
        provider_post_id: str,
        credentials: dict[str, Any],
    ) -> SocialAnalyticsResult:
        token = credentials.get("access_token")
        now = datetime.now(timezone.utc)
        if not token:
            return SocialAnalyticsResult(
                provider_post_id=provider_post_id,
                platform="INSTAGRAM",
                retrieved_at=now,
                available=False,
                error_message="Missing access token for Instagram analytics",
            )

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                # 1. Fetch public fields (likes, comments)
                fields_resp = await client.get(
                    f"{self.base_url}/{provider_post_id}",
                    params={"access_token": token, "fields": "like_count,comments_count"},
                )
                metrics = {}
                if fields_resp.status_code == 200:
                    f_data = fields_resp.json()
                    metrics["likes"] = int(f_data.get("like_count") or 0)
                    metrics["comments"] = int(f_data.get("comments_count") or 0)

                # 2. Fetch insights (impressions, reach, saved, engagement)
                insights_resp = await client.get(
                    f"{self.base_url}/{provider_post_id}/insights",
                    params={"access_token": token, "metric": "impressions,reach,saved,engagement"},
                )
                raw_insights = {}
                if insights_resp.status_code == 200:
                    raw_insights = insights_resp.json()
                    for item in raw_insights.get("data", []):
                        name = item.get("name")
                        values = item.get("values", [{}])
                        val = values[0].get("value") if values else 0
                        metrics[name] = int(val or 0)

                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="INSTAGRAM",
                    retrieved_at=now,
                    metrics=metrics,
                    raw_response={"fields": fields_resp.json() if fields_resp.status_code == 200 else {}, "insights": raw_insights},
                    available=True,
                )
            except Exception as exc:
                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="INSTAGRAM",
                    retrieved_at=now,
                    available=False,
                    error_message=str(exc),
                )
