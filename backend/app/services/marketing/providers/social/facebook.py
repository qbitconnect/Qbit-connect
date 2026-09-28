"""Official Meta Facebook Pages provider adapter (Phase 8 §3).

Communicates with Meta Graph API v20.0 for Facebook Page publishing,
account discovery, and real post insights.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.logging import get_logger, redact
from app.services.marketing.providers.base import ErrorClass
from app.services.marketing.providers.social.base import (
    BaseSocialProvider,
    SocialAnalyticsResult,
    SocialPublishResult,
)

logger = get_logger("qbit.social.facebook")

DEFAULT_GRAPH_VERSION = "v20.0"
GRAPH_BASE_URL = "https://graph.facebook.com"


class MetaFacebookProvider(BaseSocialProvider):
    provider_id: str = "meta_facebook"
    platform: str = "FACEBOOK"

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
        if not config.get("page_id"):
            problems.append("page_id is required")
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
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_posts",
            "pages_read_user_content",
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
            raise ValueError("Meta App ID and App Secret are required for OAuth token exchange")

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
                raise RuntimeError(f"Facebook token exchange failed: {err}")
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
        user_token = credentials.get("access_token")
        if not user_token:
            return []
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/me/accounts",
                params={"access_token": user_token, "fields": "id,name,category,access_token,tasks"},
            )
            if resp.status_code != 200:
                logger.warning(f"Failed to discover Facebook Pages: {resp.text}")
                return []
            data = resp.json().get("data", [])
            accounts = []
            for item in data:
                accounts.append({
                    "platform": "FACEBOOK",
                    "account_type": "PAGE",
                    "account_id": str(item.get("id")),
                    "account_name": str(item.get("name")),
                    "access_token": item.get("access_token"),
                    "tasks": item.get("tasks", []),
                })
            return accounts

    async def get_account_permissions(
        self,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        page_id = account_config.get("page_id")
        token = credentials.get("access_token")
        if not page_id or not token:
            return {"can_post": False, "tasks": []}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/{page_id}",
                params={"access_token": token, "fields": "tasks"},
            )
            if resp.status_code == 200:
                tasks = resp.json().get("tasks", [])
                return {"can_post": "CREATE_CONTENT" in tasks or "MANAGE" in tasks, "tasks": tasks}
            return {"can_post": False, "tasks": []}

    # --- Publishing -----------------------------------------------------------

    async def publish_text_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        page_id = account_config.get("page_id")
        token = credentials.get("access_token")
        if not page_id or not token:
            return SocialPublishResult.failure(
                "Missing page_id or access_token for Facebook post",
                error_class=ErrorClass.CONFIGURATION,
            )

        endpoint = f"{self.base_url}/{page_id}/feed"
        payload = {"message": text, "access_token": token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(endpoint, data=payload)
                if resp.status_code in (200, 201):
                    post_id = resp.json().get("id")
                    url = f"https://www.facebook.com/{post_id}" if post_id else None
                    return SocialPublishResult.success(post_id, post_url=url)
                err_data = resp.json().get("error", {})
                return SocialPublishResult.failure(
                    err_data.get("message", resp.text),
                    code=str(err_data.get("code", "FB_PUBLISH_ERROR")),
                    error_class=ErrorClass.PERMANENT if err_data.get("is_transient") is False else ErrorClass.TRANSIENT,
                )
            except Exception as exc:
                return SocialPublishResult.failure(str(exc), error_class=ErrorClass.TRANSIENT)

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
        page_id = account_config.get("page_id")
        token = credentials.get("access_token")
        if not page_id or not token:
            return SocialPublishResult.failure(
                "Missing page_id or access_token for Facebook photo",
                error_class=ErrorClass.CONFIGURATION,
            )

        endpoint = f"{self.base_url}/{page_id}/photos"
        payload = {
            "url": media_url,
            "caption": text,
            "access_token": token,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                resp = await client.post(endpoint, data=payload)
                if resp.status_code in (200, 201):
                    res_json = resp.json()
                    post_id = res_json.get("post_id") or res_json.get("id")
                    url = f"https://www.facebook.com/{post_id}" if post_id else None
                    return SocialPublishResult.success(post_id, post_url=url, metadata=res_json)
                err_data = resp.json().get("error", {})
                return SocialPublishResult.failure(
                    err_data.get("message", resp.text),
                    code=str(err_data.get("code", "FB_PHOTO_ERROR")),
                )
            except Exception as exc:
                return SocialPublishResult.failure(str(exc), error_class=ErrorClass.TRANSIENT)

    async def publish_video_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        video_url: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        page_id = account_config.get("page_id")
        token = credentials.get("access_token")
        if not page_id or not token:
            return SocialPublishResult.failure(
                "Missing page_id or access_token for Facebook video",
                error_class=ErrorClass.CONFIGURATION,
            )

        endpoint = f"{self.base_url}/{page_id}/videos"
        payload = {
            "file_url": video_url,
            "description": text,
            "access_token": token,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(endpoint, data=payload)
                if resp.status_code in (200, 201):
                    res_json = resp.json()
                    post_id = res_json.get("id")
                    url = f"https://www.facebook.com/{post_id}" if post_id else None
                    return SocialPublishResult.success(post_id, post_url=url, metadata=res_json)
                err_data = resp.json().get("error", {})
                return SocialPublishResult.failure(
                    err_data.get("message", resp.text),
                    code=str(err_data.get("code", "FB_VIDEO_ERROR")),
                )
            except Exception as exc:
                return SocialPublishResult.failure(str(exc), error_class=ErrorClass.TRANSIENT)

    async def publish_carousel_or_multi_media_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        media_items: list[dict[str, Any]],
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        if not media_items:
            return await self.publish_text_post(
                account_config=account_config,
                text=text,
                credentials=credentials,
                idempotency_key=idempotency_key,
            )
        # If single item, fallback to standard image
        if len(media_items) == 1:
            first = media_items[0]
            return await self.publish_image_post(
                account_config=account_config,
                text=text,
                media_url=first.get("url", ""),
                alt_text=first.get("alt_text"),
                credentials=credentials,
                idempotency_key=idempotency_key,
            )
        # For multiple items on Facebook Pages, upload unpublished photos then create feed story with attached_media
        page_id = account_config.get("page_id")
        token = credentials.get("access_token")
        if not page_id or not token:
            return SocialPublishResult.failure("Missing page_id or credentials", error_class=ErrorClass.CONFIGURATION)

        photo_ids = []
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                for item in media_items[:10]:
                    up_resp = await client.post(
                        f"{self.base_url}/{page_id}/photos",
                        data={"url": item.get("url"), "published": "false", "access_token": token},
                    )
                    if up_resp.status_code in (200, 201):
                        pid = up_resp.json().get("id")
                        if pid:
                            photo_ids.append(pid)
                    else:
                        logger.warning(f"Failed to upload photo item: {up_resp.text}")

                if not photo_ids:
                    return SocialPublishResult.failure("Failed to upload carousel media items")

                feed_data = {"message": text, "access_token": token}
                for i, pid in enumerate(photo_ids):
                    feed_data[f"attached_media[{i}]"] = f'{{"media_fbid":"{pid}"}}'

                post_resp = await client.post(f"{self.base_url}/{page_id}/feed", data=feed_data)
                if post_resp.status_code in (200, 201):
                    post_id = post_resp.json().get("id")
                    url = f"https://www.facebook.com/{post_id}" if post_id else None
                    return SocialPublishResult.success(post_id, post_url=url)
                return SocialPublishResult.failure(f"Carousel post failed: {post_resp.text}")
            except Exception as exc:
                return SocialPublishResult.failure(str(exc), error_class=ErrorClass.TRANSIENT)

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
                params={"access_token": token, "fields": "id,created_time,status_type"},
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
                platform="FACEBOOK",
                retrieved_at=now,
                available=False,
                error_message="Missing access token for analytics",
            )

        metrics = ["post_impressions", "post_engaged_users", "post_reactions_by_type_total", "post_clicks"]
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/{provider_post_id}/insights",
                    params={"access_token": token, "metric": ",".join(metrics)},
                )
                if resp.status_code != 200:
                    return SocialAnalyticsResult(
                        provider_post_id=provider_post_id,
                        platform="FACEBOOK",
                        retrieved_at=now,
                        available=False,
                        error_message=resp.json().get("error", {}).get("message", resp.text),
                    )
                raw = resp.json()
                parsed_metrics = {}
                for item in raw.get("data", []):
                    name = item.get("name")
                    values = item.get("values", [{}])
                    val = values[0].get("value") if values else None
                    if name == "post_impressions":
                        parsed_metrics["impressions"] = int(val or 0)
                    elif name == "post_engaged_users":
                        parsed_metrics["reach"] = int(val or 0)
                        parsed_metrics["engagement"] = int(val or 0)
                    elif name == "post_clicks":
                        parsed_metrics["clicks"] = int(val or 0)
                    elif name == "post_reactions_by_type_total":
                        reactions = val if isinstance(val, dict) else {}
                        parsed_metrics["reactions"] = sum(int(v or 0) for v in reactions.values())

                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="FACEBOOK",
                    retrieved_at=now,
                    metrics=parsed_metrics,
                    raw_response=raw,
                    available=True,
                )
            except Exception as exc:
                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="FACEBOOK",
                    retrieved_at=now,
                    available=False,
                    error_message=str(exc),
                )

    def validate_webhook(
        self,
        *,
        raw_body: bytes,
        signature_header: str | None,
        app_secret: str,
    ) -> bool:
        if not signature_header or not app_secret:
            return False
        if not signature_header.lower().startswith("sha256="):
            return False
        expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        provided = signature_header.split("=", 1)[1].strip().lower()
        return hmac.compare_digest(expected, provided)
