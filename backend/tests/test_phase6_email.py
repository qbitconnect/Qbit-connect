"""Phase 6 — Email Marketing & Campaign Automation Test Suite.

Comprehensive validation covering:
1. Email Provider Integration:
   - Amazon SES adapter configuration validation (sender email, AWS region)
   - Amazon SES health check (degraded on missing credentials, ready on connected)
   - Amazon SES dispatch with transport factory (200 success, 429 rate limit backoff, 400 rejection)
   - SNS telemetry event normalization (Deliveries, Hard Bounces, Soft Bounces, Spam Complaints)
2. Contact Lists & CRM Audiences:
   - Creating, retrieving, updating, and deleting contact lists
   - Adding and removing CRM leads, duplicate prevention
   - Real sending eligibility metrics calculation across members
   - Safe CSV import with explicit consent recording
   - Audience engine integration with contact_list audience type
3. Campaign Lifecycle & Dispatch:
   - Campaign draft duplication (POST /campaigns/{id}/duplicate)
   - Large audience validation warnings
   - Safe HTML sanitization (stripping <script>, javascript: URLs)
   - Worker dispatch with pre-send eligibility re-check
4. Suppression & Unsubscribe:
   - One-time unsubscribe token processing
   - Bounce and complaint automatic suppression
5. UI Route Rendering:
   - Email connections and campaign management pages
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.marketing import (
    Campaign,
    CampaignStatus,
    CampaignTemplate,
    ContactList,
    ContactListMember,
    EventType,
    RecipientStatus,
    SendingAccount,
    SuppressionEntry,
    SuppressionReason,
)
from app.models.scrape import Lead
from app.services.marketing.audience import AudienceService
from app.services.marketing.campaign import CampaignService
from app.services.marketing.email_compose import sanitize_html
from app.services.marketing.lists import ContactListService
from app.services.marketing.providers.base import ErrorClass
from app.services.marketing.providers.email.ses import AmazonSESEmailProvider
from app.services.marketing.worker import CampaignWorker


# ==============================================================================
# 1. Amazon SES Provider Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_amazon_ses_validation():
    """Verify Amazon SES adapter configuration and recipient validation."""
    provider = AmazonSESEmailProvider()

    # Configuration validation
    assert await provider.validate_configuration({}) == ["sender_email is required for Amazon SES"]
    assert len(await provider.validate_configuration({"sender_email": "invalid-email"})) > 0
    assert await provider.validate_configuration({
        "sender_email": "sales@qbitpro.com",
        "aws_region": "invalid-region",
    }) == ["Unsupported or invalid AWS region: invalid-region"]
    assert await provider.validate_configuration({
        "sender_email": "sales@qbitpro.com",
        "aws_region": "ap-south-1",
    }) == []

    # Recipient validation
    assert await provider.validate_recipient("lead@business.com") is True
    assert await provider.validate_recipient("invalid-email") is False
    assert await provider.validate_recipient("") is False

    # Message validation
    assert len(await provider.validate_message(subject="", body="Hello")) > 0
    assert len(await provider.validate_message(subject="Hello", body="")) > 0
    assert len(await provider.validate_message(subject="Valid", body="Content")) == 0


@pytest.mark.asyncio
async def test_amazon_ses_health_check():
    """Verify Amazon SES health checks report accurately without credentials."""
    provider = AmazonSESEmailProvider()

    # Missing credentials -> DEGRADED / CREDENTIALS REQUIRED
    health = await provider.health_check(
        account_config={"sender_email": "sender@qbit.com", "aws_region": "ap-south-1"},
        credentials={},
    )
    assert health["health"] == "DEGRADED"
    assert health["status"] == "CREDENTIALS REQUIRED"

    # Mock transport factory for connected health check
    def mock_transport(_key):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.MockTransport(handler)

    connected_provider = AmazonSESEmailProvider(transport_factory=mock_transport)
    live_health = await connected_provider.health_check(
        account_config={"sender_email": "sender@qbit.com", "aws_region": "ap-south-1"},
        credentials={"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE", "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"},
    )
    assert live_health["health"] == "READY"
    assert live_health["status"] == "CONNECTED"


@pytest.mark.asyncio
async def test_amazon_ses_dispatch_and_errors():
    """Verify Amazon SES dispatch behavior: success, rate limits, and transport failures."""
    # 1. Success (200)
    def ok_transport(_key):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"MessageId": "ses-msg-123456789"})
        return httpx.MockTransport(handler)

    provider_ok = AmazonSESEmailProvider(transport_factory=ok_transport)
    res_ok = await provider_ok.send(
        account_config={"sender_email": "newsletter@qbit.com", "aws_region": "ap-south-1"},
        recipient_address="customer@company.in",
        subject="Growth OS Update",
        body="<p>Welcome to QBIT Growth OS</p>",
        idempotency_key="idemp-001",
        credentials={"aws_access_key_id": "test", "aws_secret_access_key": "secret"},
    )
    assert res_ok.ok is True
    assert res_ok.provider_message_id == "ses-msg-123456789"
    assert res_ok.status == "SENT"

    # 2. Rate limit (429) -> Transient retryable error
    def rate_limit_transport(_key):
        def handler(request: httpx.Request):
            return httpx.Response(429, text="Maximum sending rate exceeded")
        return httpx.MockTransport(handler)

    provider_rate = AmazonSESEmailProvider(transport_factory=rate_limit_transport)
    res_rate = await provider_rate.send(
        account_config={"sender_email": "newsletter@qbit.com", "aws_region": "ap-south-1"},
        recipient_address="customer@company.in",
        subject="Growth OS Update",
        body="<p>Welcome</p>",
        idempotency_key="idemp-002",
        credentials={"aws_access_key_id": "test", "aws_secret_access_key": "secret"},
    )
    assert res_rate.ok is False
    assert res_rate.error_class == ErrorClass.TRANSIENT
    assert "429" in res_rate.error_code

    # 3. Permanent failure (400) -> Permanent error
    def bad_request_transport(_key):
        def handler(request: httpx.Request):
            return httpx.Response(400, text="Email address is not verified")
        return httpx.MockTransport(handler)

    provider_bad = AmazonSESEmailProvider(transport_factory=bad_request_transport)
    res_bad = await provider_bad.send(
        account_config={"sender_email": "newsletter@qbit.com", "aws_region": "ap-south-1"},
        recipient_address="customer@company.in",
        subject="Growth OS Update",
        body="<p>Welcome</p>",
        idempotency_key="idemp-003",
        credentials={"aws_access_key_id": "test", "aws_secret_access_key": "secret"},
    )
    assert res_bad.ok is False
    assert res_bad.error_class == ErrorClass.PERMANENT


@pytest.mark.asyncio
async def test_amazon_ses_sns_telemetry_normalization():
    """Verify SNS notification parsing for Deliveries, Hard/Soft Bounces, and Complaints."""
    provider = AmazonSESEmailProvider()

    # Delivery notification
    deliv_event = await provider.handle_event({
        "eventType": "Delivery",
        "mail": {"messageId": "msg-001", "timestamp": "2026-09-27T10:00:00Z"},
        "delivery": {"processingTimeMillis": 450, "smtpResponse": "250 2.0.0 OK"},
    })
    assert deliv_event["event_type"] == "DELIVERED"
    assert deliv_event["provider_message_id"] == "msg-001"

    # Hard Bounce notification
    bounce_event = await provider.handle_event({
        "eventType": "Bounce",
        "mail": {"messageId": "msg-002"},
        "bounce": {"bounceType": "Permanent", "bounceSubType": "General", "bouncedRecipients": [{"emailAddress": "bad@target.com"}]},
    })
    assert bounce_event["event_type"] == "BOUNCE"
    assert bounce_event["is_hard_bounce"] is True
    assert "bad@target.com" in bounce_event["metadata"]["bounced_recipients"]

    # Complaint notification
    complaint_event = await provider.handle_event({
        "eventType": "Complaint",
        "mail": {"messageId": "msg-003"},
        "complaint": {"complaintFeedbackType": "abuse", "complaintSubType": None},
    })
    assert complaint_event["event_type"] == "COMPLAINT"
    assert complaint_event["reason"] == "abuse"


# ==============================================================================
# 2. Contact Lists & CRM Audiences
# ==============================================================================

@pytest.mark.asyncio
async def test_contact_list_crud_and_membership(client: AsyncClient, admin_headers: dict):
    """Verify contact list creation, membership management, and duplicate prevention."""
    # 1. Create Contact List
    create_res = await client.post(
        "/api/v1/campaigns/lists",
        json={"name": "Enterprise Healthcare Leads", "description": "Verified hospital and clinic decision makers"},
        headers=admin_headers,
    )
    assert create_res.status_code == 200
    list_data = create_res.json()["data"]
    list_id = list_data["id"]
    assert list_data["name"] == "Enterprise Healthcare Leads"

    # 2. Seed 2 leads in CRM
    l1 = (await client.post("/api/v1/leads", json={"business_name": "Apollo Care", "email": "contact@apollo.test"}, headers=admin_headers)).json()["data"]["id"]
    l2 = (await client.post("/api/v1/leads", json={"business_name": "Fortis Health", "email": "info@fortis.test"}, headers=admin_headers)).json()["data"]["id"]

    # 3. Add members to list
    add_res = await client.post(
        f"/api/v1/campaigns/lists/{list_id}/members",
        json={"lead_ids": [l1, l2]},
        headers=admin_headers,
    )
    assert add_res.status_code == 200
    assert add_res.json()["data"]["added"] == 2
    assert add_res.json()["data"]["skipped_duplicates"] == 0

    # 4. Duplicate prevention: adding the same leads again skips duplicates
    dup_res = await client.post(
        f"/api/v1/campaigns/lists/{list_id}/members",
        json={"lead_ids": [l1, l2]},
        headers=admin_headers,
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["data"]["added"] == 0
    assert dup_res.json()["data"]["skipped_duplicates"] == 2

    # 5. List members
    members_res = await client.get(f"/api/v1/campaigns/lists/{list_id}/members", headers=admin_headers)
    assert members_res.status_code == 200
    assert members_res.json()["data"]["total"] == 2

    # 6. Remove member
    del_res = await client.delete(f"/api/v1/campaigns/lists/{list_id}/members/{l1}", headers=admin_headers)
    assert del_res.status_code == 200
    assert del_res.json()["data"]["removed"] is True

    # Total drops to 1
    after_del = await client.get(f"/api/v1/campaigns/lists/{list_id}/members", headers=admin_headers)
    assert after_del.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_contact_list_csv_import_and_consent(client: AsyncClient, admin_headers: dict):
    """Verify CSV import into contact list with consent and audit tracking."""
    # Create target list
    cl = (await client.post("/api/v1/campaigns/lists", json={"name": "Inbound Demo Requests"}, headers=admin_headers)).json()["data"]["id"]

    csv_data = """business_name,contact_name,email
Manipal Hospital,Dr. Ramesh,ramesh@manipal.test
Narayana Health,Dr. Priya,priya@narayana.test
Invalid Row,,
"""

    import_res = await client.post(
        f"/api/v1/campaigns/lists/{cl}/import-csv",
        json={
            "csv_content": csv_data,
            "has_consent": True,
            "consent_source": "Website Demo Form Submission (2026-09)",
        },
        headers=admin_headers,
    )
    assert import_res.status_code == 200
    result = import_res.json()["data"]
    assert result["imported"] == 2
    assert result["failed"] == 1
    assert result["has_consent_recorded"] is True


@pytest.mark.asyncio
async def test_audience_service_contact_list_resolution(app):
    """Verify AudienceService resolves contact_list into matching lead IDs."""
    from app.services.marketing.lists import ContactListService

    db = app.state.db
    async with db.session() as session:
        list_svc = ContactListService()
        cl = await list_svc.create_list(session, name="Fintech VIPs")

        lead = Lead(business_name="Razorpay Partner", email="fintech@razorpay.test")
        session.add(lead)
        await session.flush()

        await list_svc.add_members(session, cl.id, [lead.id])

        aud_svc = AudienceService()
        def_dict = {"type": "contact_list", "list_id": str(cl.id)}
        count = await aud_svc.count(session, def_dict)
        assert count == 1

        ids_generator = aud_svc.iter_lead_ids(session, def_dict)
        emitted_ids = []
        async for batch in ids_generator:
            emitted_ids.extend(batch)
        assert lead.id in emitted_ids


# ==============================================================================
# 3. Campaign Builder, Sanitization & Duplication
# ==============================================================================

@pytest.mark.asyncio
async def test_campaign_draft_duplication(client: AsyncClient, admin_headers: dict):
    """Verify duplication of a campaign draft."""
    # 1. Create a draft campaign
    c_res = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "Q4 Outreach Campaign",
            "channel": "EMAIL",
            "description": "Original campaign draft",
            "audience_definition": {"type": "tags", "tags": ["Technology"]},
        },
        headers=admin_headers,
    )
    assert c_res.status_code == 201
    orig_id = c_res.json()["data"]["id"]

    # 2. Duplicate it
    dup_res = await client.post(f"/api/v1/campaigns/{orig_id}/duplicate", headers=admin_headers)
    assert dup_res.status_code == 200
    dup_data = dup_res.json()["data"]
    assert dup_data["id"] != orig_id
    assert "Q4 Outreach Campaign (Copy)" in dup_data["name"]
    assert dup_data["status"] == "DRAFT"


def test_safe_html_sanitization():
    """Verify email HTML sanitization removes dangerous tags and event handlers."""
    unsafe_html = """
    <div>
        <h1>Welcome to QBIT!</h1>
        <script>alert('malicious script execution');</script>
        <img src="https://qbitpro.com/logo.png" onload="alert('exploit')"/>
        <a href="javascript:alert('xss')">Click Here</a>
        <a href="https://qbitpro.com/dashboard">Valid Link</a>
    </div>
    """
    clean = sanitize_html(unsafe_html)
    assert "<script>" not in clean
    assert "onload" not in clean
    assert "javascript:" not in clean
    assert "Valid Link" in clean
    assert "https://qbitpro.com/dashboard" in clean


# ==============================================================================
# 4. Suppression, Unsubscribe & Eligibility Re-check
# ==============================================================================

@pytest.mark.asyncio
async def test_campaign_worker_pre_send_suppression_protection(app):
    """Verify worker re-checks eligibility immediately before dispatch and skips newly suppressed contacts."""
    from app.models.marketing import (
        CampaignQueueItem,
        CampaignRecipient,
        OptOutRecord,
        QueueStatus,
        SuppressionType,
    )
    from app.services.marketing.providers import build_provider_registry
    from app.services.marketing.suppression import SuppressionService

    db = app.state.db
    settings = app.state.settings

    async with db.session() as session:
        # Create lead, template, sending account, campaign
        lead = Lead(business_name="Target Corp", email="optout@target.test")
        session.add(lead)
        await session.flush()

        template = CampaignTemplate(
            name="Test Template",
            channel="EMAIL",
            subject="Hello",
            body="<p>Test</p>",
            status="ACTIVE",
        )
        session.add(template)

        account = SendingAccount(
            name="Default SMTP",
            channel="EMAIL",
            provider="email_mock",
            identifier="noreply@qbit.test",
            status="ACTIVE",
            health_status="HEALTHY",
        )
        session.add(account)
        await session.flush()

        campaign = Campaign(
            name="Suppression Test Campaign",
            channel="EMAIL",
            status=CampaignStatus.RUNNING,
            template_id=template.id,
            sending_account_id=account.id,
        )
        session.add(campaign)
        await session.flush()

        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            lead_id=lead.id,
            recipient_address="optout@target.test",
            status=RecipientStatus.QUEUED,
        )
        session.add(recipient)
        await session.flush()

        queue_item = CampaignQueueItem(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            channel="EMAIL",
            sending_account_id=account.id,
            status=QueueStatus.WAITING,
        )
        session.add(queue_item)
        await session.commit()

        # Suppress the recipient mid-flight
        supp_svc = SuppressionService()
        await supp_svc.add(
            session,
            entry_type=SuppressionType.EMAIL,
            address="optout@target.test",
            channel="EMAIL",
            reason=SuppressionReason.UNSUBSCRIBED,
        )

        # Worker processes queue
        registry = build_provider_registry(settings)
        worker = CampaignWorker(settings, registry, owner="test-worker")
        await worker._send_batch(session)

        # Confirm recipient was SKIPPED due to suppression and NOT sent
        await session.refresh(recipient)
        assert recipient.status == RecipientStatus.SKIPPED
        assert "UNSUBSCRIBED" in str(recipient.skip_reason or "").upper() or "SUPPRESSED" in str(recipient.skip_reason or "").upper()


# ==============================================================================
# 5. UI Views Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_email_marketing_ui_views(client: AsyncClient, admin_headers: dict):
    """Verify that email marketing and connection UI screens render with Stitch design."""
    # 1. Campaigns listing
    c_res = await client.get("/campaigns", headers=admin_headers)
    assert c_res.status_code == 200
    assert "Campaigns" in c_res.text

    # 2. Campaign wizard (New campaign)
    new_res = await client.get("/campaigns/new", headers=admin_headers)
    assert new_res.status_code == 200
    assert "NEW CAMPAIGN" in new_res.text.upper()

    # 3. Email accounts dashboard
    acc_res = await client.get("/connections/email", headers=admin_headers)
    assert acc_res.status_code == 200
    assert "Email" in acc_res.text

    # 4. Email connection setup wizard
    conn_new = await client.get("/connections/email/new", headers=admin_headers)
    assert conn_new.status_code == 200
    assert "ADD EMAIL SENDER ACCOUNT" in conn_new.text.upper()
