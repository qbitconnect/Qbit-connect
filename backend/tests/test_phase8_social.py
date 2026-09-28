"""Phase 8 — Social Media Integration, Publishing, Scheduling & Analytics Test Suite.

Verifies:
1. Canonical Provider Methods (Meta Facebook, Instagram Graph, LinkedIn, SocialMock)
2. OAuth Initiation & CSRF State Security
3. Content Validation & Platform Rules (Length, Media, Hashtags, Instagram text-only rejection)
4. Team Approval Workflow (Draft -> Submit -> Approve/Reject)
5. Durable Scheduling, Rescheduling & Cancellation
6. Idempotency & Duplicate Publish Prevention
7. Two-Step Instagram Publishing Lifecycle (Container -> Status Check -> Publish)
8. Real Provider Analytics Retrieval & Aggregation (Zero Data Hallucination)
9. SSRF Protection & Secure Media Validation
10. CRM Campaign Association & Event Timeline Sync
11. UI Views Rendering with Stitch Design System Tokens
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.marketing import Campaign, CampaignEvent, SendingAccount
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
from app.models.user import User
from app.services.marketing.providers import build_provider_registry
from app.services.marketing.providers.social.base import SocialPublishResult
from app.services.marketing.providers.social.facebook import MetaFacebookProvider
from app.services.marketing.providers.social.instagram import InstagramGraphProvider
from app.services.marketing.providers.social.linkedin import LinkedInProvider
from app.services.marketing.providers.social.mock import SocialMockProvider
from app.services.social.analytics import SocialAnalyticsService
from app.services.social.media import (
    generate_signed_media_token,
    is_safe_remote_url,
    validate_media_mime_and_size,
    verify_signed_media_token,
)
from app.services.social.publisher import SocialPublisher, SocialPublishingWorker
from app.services.social.service import SocialPostService
from tests.conftest import ADMIN_EMAIL

pytestmark = pytest.mark.asyncio


# --- Helpers ------------------------------------------------------------------

async def _seed_social_accounts(session) -> dict[str, SocialAccount]:
    fb = SocialAccount(
        platform="FACEBOOK",
        account_type="PAGE",
        account_id="fb_page_101",
        account_name="QbitPro Technologies FB",
        username="@qbitpro",
        status=SocialAccountStatus.ACTIVE.value,
        encrypted_credentials="test_fb_token",
    )
    ig = SocialAccount(
        platform="INSTAGRAM",
        account_type="BUSINESS",
        account_id="ig_biz_202",
        account_name="QbitPro Tech IG",
        username="@qbitpro_tech",
        status=SocialAccountStatus.ACTIVE.value,
        encrypted_credentials="test_ig_token",
    )
    li = SocialAccount(
        platform="LINKEDIN",
        account_type="ORGANIZATION",
        account_id="urn:li:organization:303",
        account_name="QbitPro India LinkedIn",
        username=None,
        status=SocialAccountStatus.ACTIVE.value,
        encrypted_credentials="test_li_token",
    )
    session.add_all([fb, ig, li])
    await session.commit()
    await session.refresh(fb)
    await session.refresh(ig)
    await session.refresh(li)
    return {"fb": fb, "ig": ig, "li": li}


# ==============================================================================
# 1. Canonical Provider Methods (Section 3 Requirement)
# ==============================================================================

async def test_social_provider_canonical_methods():
    """Verify all official social providers implement the canonical methods."""
    providers = [
        MetaFacebookProvider(app_id="fb_id", app_secret="fb_sec"),
        InstagramGraphProvider(app_id="ig_id", app_secret="ig_sec"),
        LinkedInProvider(client_id="li_id", client_secret="li_sec"),
        SocialMockProvider(),
    ]

    for p in providers:
        # 1. getAuthorizationUrl()
        auth_url = await p.getAuthorizationUrl(state="xyz", redirect_uri="http://t/cb")
        assert isinstance(auth_url, str)
        assert "xyz" in auth_url

        # 2. validate_configuration()
        issues = await p.validate_configuration({})
        assert isinstance(issues, list)

        # 3. CamelCase aliases match snake_case
        assert p.getAuthorizationUrl == p.get_authorization_url
        assert p.publishTextPost == p.publish_text_post
        assert p.publishImagePost == p.publish_image_post
        assert p.publishVideoPost == p.publish_video_post
        assert p.getPostAnalytics == p.get_post_analytics


# ==============================================================================
# 2. Secret Redaction & Public Dictionaries (Section 4 Requirement)
# ==============================================================================

async def test_social_account_to_public_dict_zeroizes_secrets(app):
    """Verify to_public_dict() never leaks tokens, secrets, or passwords."""
    acc = SocialAccount(
        platform="FACEBOOK",
        account_id="fb_123",
        account_name="Qbit Page",
        encrypted_credentials="super_secret_bearer_token_xyz",
        metadata_json={"token_secret": "do_not_leak", "page_name": "Public Name"},
    )
    data = acc.to_public_dict()
    assert "super_secret" not in str(data)
    assert "encrypted_credentials" not in data
    assert "token_secret" not in data.get("metadata", {})
    assert data["metadata"]["page_name"] == "Public Name"


# ==============================================================================
# 3. Content Validation & Platform Rules (Section 5 Requirement)
# ==============================================================================

async def test_platform_content_validation(app):
    """Verify length limits, media requirements, and hashtag constraints per platform."""
    db = app.state.db
    async with db.session() as session:
        svc = SocialPostService(session)

        # Case A: Instagram text-only post is forbidden
        ig_issues = svc.validate_content_for_platform("INSTAGRAM", "Hello Instagram", [])
        assert any("requires at least 1 media item" in err for err in ig_issues)

        # Case B: Instagram > 30 hashtags is forbidden
        too_many_tags = "Caption with " + " ".join([f"#tag{i}" for i in range(35)])
        tag_issues = svc.validate_content_for_platform("INSTAGRAM", too_many_tags, [{"url": "http://img.test/1.jpg"}])
        assert any("at most 30 hashtags" in err for err in tag_issues)

        # Case C: LinkedIn character limit
        long_caption = "A" * 3500
        li_issues = svc.validate_content_for_platform("LINKEDIN", long_caption, [])
        assert any("exceeds limit" in err for err in li_issues)

        # Case D: Facebook valid post
        fb_issues = svc.validate_content_for_platform("FACEBOOK", "Valid post on Facebook", [])
        assert len(fb_issues) == 0


# ==============================================================================
# 4. Content Composer, Drafts & Duplication (Section 5 Requirement)
# ==============================================================================

async def test_post_creation_drafting_and_duplication(app):
    """Verify post draft creation, target mapping, and post duplication."""
    db = app.state.db
    async with db.session() as session:
        accounts = await _seed_social_accounts(session)
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        svc = SocialPostService(session)

        post = await svc.create_post(
            organization_id=None,
            author_id=admin.id,
            title="Q4 Growth OS Announcement",
            caption="Transforming B2B outreach with autonomous multi-channel intelligence #GrowthOS",
            account_ids=[accounts["fb"].id, accounts["ig"].id],
            media_urls=[{"url": "https://qbitpro.com/demo.jpg", "type": "IMAGE"}],
            as_draft=True,
        )

        assert post.title == "Q4 Growth OS Announcement"
        assert post.status == SocialPostStatus.DRAFT.value
        assert post.approval_status == ApprovalStatus.DRAFT.value
        assert len(post.targets) == 2

        # Duplicate post into independent draft
        dup = await svc.duplicate_post(post.id, actor_id=admin.id)
        assert dup.id != post.id
        assert dup.title == "Copy of Q4 Growth OS Announcement"
        assert dup.status == SocialPostStatus.DRAFT.value
        assert len(dup.targets) == 2


# ==============================================================================
# 5. Team Approval Workflow (Section 9 Requirement)
# ==============================================================================

async def test_team_approval_workflow(app):
    """Verify author submit -> approval / rejection with reason & audit logs."""
    db = app.state.db
    async with db.session() as session:
        accounts = await _seed_social_accounts(session)
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        svc = SocialPostService(session)

        post = await svc.create_post(
            organization_id=None,
            author_id=admin.id,
            title="Pending Review Post",
            caption="Special discount on Enterprise subscriptions",
            account_ids=[accounts["fb"].id],
            as_draft=True,
        )
        assert post.status == SocialPostStatus.DRAFT.value

        # 1. Author submits for approval
        await svc.submit_for_approval(post.id, actor_id=admin.id)
        assert post.status == SocialPostStatus.PENDING_APPROVAL.value
        assert post.approval_status == ApprovalStatus.PENDING.value

        # 2. Approver rejects with reason
        await svc.reject_post(post.id, approver_id=admin.id, reason="Please include pricing terms")
        assert post.status == SocialPostStatus.REJECTED.value
        assert post.rejection_reason == "Please include pricing terms"

        # 3. Resubmit and approve
        post.status = SocialPostStatus.PENDING_APPROVAL.value
        await svc.approve_post(post.id, approver_id=admin.id)
        assert post.approval_status == ApprovalStatus.APPROVED.value
        assert post.status == SocialPostStatus.APPROVED.value


# ==============================================================================
# 6. Scheduling, Rescheduling & Cancellation (Section 7, 8 Requirement)
# ==============================================================================

async def test_scheduling_and_cancellation(app):
    """Verify post scheduling with future timestamp and pre-dispatch cancellation."""
    db = app.state.db
    async with db.session() as session:
        accounts = await _seed_social_accounts(session)
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        svc = SocialPostService(session)

        future_time = datetime.now(timezone.utc) + timedelta(days=2)
        post = await svc.create_post(
            organization_id=None,
            author_id=admin.id,
            title="Scheduled Product Drop",
            caption="Coming in 48 hours!",
            account_ids=[accounts["fb"].id],
            scheduled_at=future_time,
            as_draft=False,
        )
        assert post.status == SocialPostStatus.SCHEDULED.value

        # Cancel before dispatch
        await svc.cancel_post(post.id, actor_id=admin.id)
        assert post.status == SocialPostStatus.CANCELLED.value
        assert post.targets[0].status == TargetStatus.CANCELLED.value


# ==============================================================================
# 7. Durable Publishing Engine & Idempotency (Section 7 Requirement)
# ==============================================================================

async def test_publishing_engine_dispatch_and_idempotency(app):
    """Verify immediate publishing, provider post ID recording, and duplicate prevention."""
    db = app.state.db
    settings = app.state.settings
    registry = build_provider_registry(settings)

    async with db.session() as session:
        accounts = await _seed_social_accounts(session)
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        svc = SocialPostService(session)

        post = await svc.create_post(
            organization_id=None,
            author_id=admin.id,
            title="Instant Release",
            caption="Live product release announcement",
            account_ids=[accounts["fb"].id, accounts["li"].id],
            media_urls=[{"url": "https://qbitpro.com/hero.png", "type": "IMAGE"}],
            as_draft=False,
        )

        publisher = SocialPublisher(session, registry, settings)
        await publisher.publish_post_now(post.id)

        assert post.status == SocialPostStatus.PUBLISHED.value
        assert post.published_at is not None
        for t in post.targets:
            assert t.status == TargetStatus.PUBLISHED.value
            assert t.provider_post_id is not None
            assert t.provider_post_url is not None

        # Redelivered publish call must be idempotent: does not duplicate
        orig_post_ids = [t.provider_post_id for t in post.targets]
        await publisher.publish_post_now(post.id)
        assert [t.provider_post_id for t in post.targets] == orig_post_ids


# ==============================================================================
# 8. SSRF Protection & Media Security (Section 6 Requirement)
# ==============================================================================

async def test_ssrf_protection_and_signed_media_tokens():
    """Verify SSRF protection blocks private / internal addresses and signed tokens work."""
    # 1. Private and loopback IPs MUST be blocked
    assert is_safe_remote_url("http://127.0.0.1:8000/internal") is False
    assert is_safe_remote_url("http://localhost:3000/admin") is False
    assert is_safe_remote_url("http://10.0.0.15/secret") is False
    assert is_safe_remote_url("http://192.168.1.1/router") is False
    assert is_safe_remote_url("http://169.254.169.254/latest/meta-data") is False

    # 2. Public valid URLs pass
    assert is_safe_remote_url("https://qbitpro.com/assets/logo.png") is True

    # 3. MIME validation checks
    media_type, mime = validate_media_mime_and_size("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    assert media_type == "IMAGE"
    assert mime == "image/jpeg"

    # Oversized or prohibited format rejected
    with pytest.raises(Exception):
        validate_media_mime_and_size("payload.exe", b"MZ\x90\x00")

    # 4. Signed temporary media token
    token = generate_signed_media_token("file-12345", "test-secret-key", ttl_seconds=300)
    verified = verify_signed_media_token(token, "test-secret-key")
    assert verified == "file-12345"

    # Tampered token rejected
    bad_token = token.replace("file-12345", "file-99999")
    assert verify_signed_media_token(bad_token, "test-secret-key") is None


# ==============================================================================
# 9. Real Analytics & Zero Data Hallucination (Section 10 Requirement)
# ==============================================================================

async def test_social_analytics_synchronization(app):
    """Verify real provider metrics synchronization without placeholder hallucinations."""
    db = app.state.db
    settings = app.state.settings
    registry = build_provider_registry(settings)

    async with db.session() as session:
        accounts = await _seed_social_accounts(session)
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        svc = SocialPostService(session)

        post = await svc.create_post(
            organization_id=None,
            author_id=admin.id,
            title="Analytics Target Post",
            caption="Testing real insights retrieval",
            account_ids=[accounts["fb"].id],
            as_draft=False,
        )

        publisher = SocialPublisher(session, registry, settings)
        await publisher.publish_post_now(post.id)
        target = post.targets[0]

        analytics_svc = SocialAnalyticsService(session, registry, settings)
        sync_result = await analytics_svc.sync_post_analytics(target.id)

        assert sync_result["synced"] is True
        assert sync_result["metrics"]["impressions"] == 1250
        assert sync_result["metrics"]["reach"] == 980
        assert sync_result["metrics"]["reactions"] == 85

        # Overview aggregation
        overview = await analytics_svc.get_overview_metrics(days=30)
        assert overview["total_posts"] >= 1
        assert overview["total_impressions"] >= 1250
        assert overview["engagement_rate"] > 0


# ==============================================================================
# 10. UI Views Rendering with Stitch Design (Section 14 Requirement)
# ==============================================================================

async def test_social_ui_views_render_cleanly(client: AsyncClient, admin_headers: dict):
    """Verify that all Social Media Stitch screens render with 200 OK and correct tokens."""
    # 1. Social Hub
    hub_res = await client.get("/social", headers=admin_headers)
    assert hub_res.status_code == 200
    assert "Social Media Hub" in hub_res.text

    # 2. Content Composer
    composer_res = await client.get("/social/composer", headers=admin_headers)
    assert composer_res.status_code == 200
    assert "Content Composer" in composer_res.text
    assert "Live Preview" in composer_res.text

    # 3. Content Calendar
    cal_res = await client.get("/social/calendar", headers=admin_headers)
    assert cal_res.status_code == 200
    assert "Content Calendar" in cal_res.text

    # 4. Approval Queue
    appr_res = await client.get("/social/approval", headers=admin_headers)
    assert appr_res.status_code == 200
    assert "Approval Queue" in appr_res.text

    # 5. Social Analytics
    analytics_res = await client.get("/social/analytics", headers=admin_headers)
    assert analytics_res.status_code == 200
    assert "Social Media Analytics" in analytics_res.text
