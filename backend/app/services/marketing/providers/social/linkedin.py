"""Official LinkedIn Community Management & Share API provider adapter (Phase 8 §3).

Communicates with LinkedIn REST APIs for personal and Organization Page
post publishing, asset upload, and share statistics.
"""

from __future__ import annotations

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

logger = get_logger("qbit.social.linkedin")

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com"


class LinkedInProvider(BaseSocialProvider):
    provider_id: str = "linkedin"
    platform: str = "LINKEDIN"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = LINKEDIN_API_BASE,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url

    async def validate_configuration(self, config: dict) -> list[str]:
        problems = []
        if not config.get("author_urn"):
            problems.append("author_urn (e.g. urn:li:organization:123 or urn:li:person:abc) is required")
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
        cid = self.client_id or "CLIENT_ID_NOT_CONFIGURED"
        default_scopes = [
            "w_member_social",
            "openid",
            "profile",
            "w_organization_social",
            "r_organization_social",
        ]
        chosen_scopes = scopes or default_scopes
        params = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(chosen_scopes),
        }
        return f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        if not self.client_id or not self.client_secret:
            raise ValueError("LinkedIn Client ID and Client Secret are required for OAuth")

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(LINKEDIN_TOKEN_URL, data=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"LinkedIn token exchange failed: {resp.text}")
            return resp.json()

    async def refresh_or_validate_token(
        self,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        token = credentials.get("access_token")
        if not token:
            return {"valid": False, "error": "Missing access token"}
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/v2/userinfo", headers=headers)
            if resp.status_code == 200:
                return {"valid": True, "data": resp.json()}
            return {"valid": False, "error": resp.text}

    async def get_connected_accounts(
        self,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        token = credentials.get("access_token")
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        accounts = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Fetch authenticated personal profile URN
            user_resp = await client.get(f"{self.base_url}/v2/userinfo", headers=headers)
            if user_resp.status_code == 200:
                u_data = user_resp.json()
                sub = u_data.get("sub")
                name = u_data.get("name") or f"{u_data.get('given_name', '')} {u_data.get('family_name', '')}".strip()
                if sub:
                    accounts.append({
                        "platform": "LINKEDIN",
                        "account_type": "PROFILE",
                        "account_id": f"urn:li:person:{sub}",
                        "account_name": name or "LinkedIn Member",
                        "username": None,
                        "profile_picture_url": u_data.get("picture"),
                        "access_token": token,
                    })

            # 2. Fetch organizational pages where user is administrator
            org_resp = await client.get(
                f"{self.base_url}/v2/organizationalEntityAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
                headers=headers,
            )
            if org_resp.status_code == 200:
                elements = org_resp.json().get("elements", [])
                for elem in elements:
                    org_urn = elem.get("organizationalTarget")
                    if org_urn:
                        # fetch org details
                        org_id = org_urn.split(":")[-1]
                        accounts.append({
                            "platform": "LINKEDIN",
                            "account_type": "ORGANIZATION",
                            "account_id": org_urn,
                            "account_name": f"Company Page ({org_id})",
                            "username": None,
                            "access_token": token,
                        })
        return accounts

    async def get_account_permissions(
        self,
        account_config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        token = credentials.get("access_token")
        author_urn = account_config.get("author_urn")
        return {"can_post": bool(token and author_urn), "author_urn": author_urn}

    # --- Publishing -----------------------------------------------------------

    async def publish_text_post(
        self,
        *,
        account_config: dict[str, Any],
        text: str,
        credentials: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SocialPublishResult:
        token = credentials.get("access_token")
        author_urn = account_config.get("author_urn")
        if not token or not author_urn:
            return SocialPublishResult.failure(
                "Missing author_urn or access_token for LinkedIn post",
                error_class=ErrorClass.CONFIGURATION,
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": "202401",
        }
        payload = {
            "author": author_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/rest/posts", json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    # Post URN is returned in the 'x-restli-id' or 'x-linkedin-id' header or body
                    post_urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id")
                    if not post_urn and resp.text:
                        post_urn = resp.json().get("id")
                    url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None
                    return SocialPublishResult.success(post_urn or "urn:li:share:published", post_url=url)

                # Fallback to ugcPosts if rest/posts is not enabled on this app
                if resp.status_code == 404:
                    return await self._publish_ugc_post(client, token, author_urn, text)

                return SocialPublishResult.failure(f"LinkedIn publish error: {resp.text}")
            except Exception as exc:
                return SocialPublishResult.failure(str(exc), error_class=ErrorClass.TRANSIENT)

    async def _publish_ugc_post(
        self,
        client: httpx.AsyncClient,
        token: str,
        author_urn: str,
        text: str,
    ) -> SocialPublishResult:
        ugc_payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        resp = await client.post(f"{self.base_url}/v2/ugcPosts", json=ugc_payload, headers=headers)
        if resp.status_code in (200, 201):
            post_urn = resp.json().get("id")
            url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None
            return SocialPublishResult.success(post_urn, post_url=url)
        return SocialPublishResult.failure(f"LinkedIn UGC publish error: {resp.text}")

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
        token = credentials.get("access_token")
        author_urn = account_config.get("author_urn")
        if not token or not author_urn:
            return SocialPublishResult.failure("Missing credentials or author_urn", error_class=ErrorClass.CONFIGURATION)

        # Initialize image upload via LinkedIn REST / rest/images
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202401",
        }
        init_payload = {"initializeUploadRequest": {"owner": author_urn}}

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                init_resp = await client.post(f"{self.base_url}/rest/images?action=initializeUpload", json=init_payload, headers=headers)
                if init_resp.status_code not in (200, 201):
                    # If REST images API unavailable, fallback to text with link
                    return await self.publish_text_post(
                        account_config=account_config,
                        text=f"{text}\n\n{media_url}",
                        credentials=credentials,
                        idempotency_key=idempotency_key,
                    )

                init_data = init_resp.json().get("value", {})
                upload_url = init_data.get("uploadUrl")
                image_urn = init_data.get("image")

                # Fetch media binary and upload to LinkedIn storage URL
                media_bytes_resp = await client.get(media_url)
                if media_bytes_resp.status_code == 200:
                    up_resp = await client.put(upload_url, content=media_bytes_resp.content, headers={"Authorization": f"Bearer {token}"})
                    if up_resp.status_code not in (200, 201):
                        logger.warning(f"LinkedIn binary upload warning: {up_resp.status_code}")

                # Create post referencing the image URN
                post_payload = {
                    "author": author_urn,
                    "commentary": text,
                    "visibility": "PUBLIC",
                    "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                    "content": {"media": {"id": image_urn, "altText": alt_text or "Image post"}},
                    "lifecycleState": "PUBLISHED",
                    "isReshareDisabledByAuthor": False,
                }
                pub_resp = await client.post(f"{self.base_url}/rest/posts", json=post_payload, headers=headers)
                if pub_resp.status_code in (200, 201):
                    post_urn = pub_resp.headers.get("x-restli-id") or pub_resp.json().get("id")
                    url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None
                    return SocialPublishResult.success(post_urn or image_urn, post_url=url)

                return SocialPublishResult.failure(f"LinkedIn image post failed: {pub_resp.text}")
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
        # Fallback to post with video URL commentary
        return await self.publish_text_post(
            account_config=account_config,
            text=f"{text}\n\n{video_url}",
            credentials=credentials,
            idempotency_key=idempotency_key,
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
        if media_items:
            return await self.publish_image_post(
                account_config=account_config,
                text=text,
                media_url=media_items[0].get("url", ""),
                credentials=credentials,
                idempotency_key=idempotency_key,
            )
        return await self.publish_text_post(
            account_config=account_config,
            text=text,
            credentials=credentials,
            idempotency_key=idempotency_key,
        )

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
        headers = {"Authorization": f"Bearer {token}", "LinkedIn-Version": "202401"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/rest/posts/{urllib.parse.quote(provider_post_id, safe='')}", headers=headers)
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
                platform="LINKEDIN",
                retrieved_at=now,
                available=False,
                error_message="Missing access token for LinkedIn analytics",
            )

        headers = {"Authorization": f"Bearer {token}", "LinkedIn-Version": "202401"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                # Query social actions (likes, comments) for the URN
                resp = await client.get(
                    f"{self.base_url}/rest/socialActions/{urllib.parse.quote(provider_post_id, safe='')}",
                    headers=headers,
                )
                metrics = {}
                if resp.status_code == 200:
                    raw = resp.json()
                    metrics["likes"] = int(raw.get("likesSummary", {}).get("totalLikes", 0))
                    metrics["comments"] = int(raw.get("commentsSummary", {}).get("totalComments", 0))
                    return SocialAnalyticsResult(
                        provider_post_id=provider_post_id,
                        platform="LINKEDIN",
                        retrieved_at=now,
                        metrics=metrics,
                        raw_response=raw,
                        available=True,
                    )
                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="LINKEDIN",
                    retrieved_at=now,
                    available=False,
                    error_message=resp.text,
                )
            except Exception as exc:
                return SocialAnalyticsResult(
                    provider_post_id=provider_post_id,
                    platform="LINKEDIN",
                    retrieved_at=now,
                    available=False,
                    error_message=str(exc),
                )
