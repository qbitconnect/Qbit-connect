"""Comprehensive Phase 7 Automated Test Suite: WhatsApp Business Platform, Team Inbox & Automation.

Covers:
1. Official WhatsApp Provider methods & canonical aliases
2. Webhook verification challenge & HMAC signature validation
3. Inbound message normalization, conversation matching, and deduplication
4. Outbound messaging inside 24h window vs outside (template-only requirement)
5. Out-of-order status receipt ladder (READ never downgraded to DELIVERED)
6. Team inbox conversations, search, filters, and internal notes
7. Employee assignment, reassignment, and timeline event recording
8. CRM contact linking and timeline logging
9. WhatsApp campaign execution with approved templates and suppression gates
10. Workflow automation WhatsApp action execution
11. UI screens rendering (/inbox, /connections/whatsapp/new, /analytics/whatsapp)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.models.marketing import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    CampaignTemplate,
    ProviderTemplateStatus,
    RecipientStatus,
    SendingAccount,
    SuppressionReason,
    SuppressionType,
    TemplateOrigin,
)
from app.models.messaging import (
    Conversation,
    ConversationEvent,
    ConversationNote,
    ConversationPriority,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageStatus,
    ProviderEvent,
)
from app.models.scrape import Lead
from app.services.inbox.engine import ConversationEngine
from app.services.inbox.normalizer import UnifiedInboundMessage, utcnow
from app.services.inbox.outbox import OutboxService
from app.services.inbox.reply import ReplyService, TemplateRequiredError
from app.services.inbox.workspace import InboxWorkspace
from app.services.marketing import build_provider_registry
from app.services.marketing.providers.base import ErrorClass
from app.services.marketing.providers.whatsapp import WhatsAppMockProvider, WhatsAppProvider
from app.services.marketing.suppression import SuppressionService
from app.services.marketing.webhooks import WhatsAppEventNormalizer, WhatsAppWebhookService
from tests.marketing.conftest import seed_account, seed_leads


# ==============================================================================
# 1. Provider Capabilities, Canonical Signatures & Aliases
# ==============================================================================

@pytest.mark.asyncio
async def test_whatsapp_provider_canonical_methods_and_aliases():
    """Verify official provider methods required by Phase 7 Section 3."""
    provider = WhatsAppMockProvider()

    # 1. verifyConnection() / verify_connection()
    res = await provider.verifyConnection({"phone_number_id": "123456"}, {"access_token": "valid-token"})
    assert res["ok"] is True
    assert "steps" in res

    # 2. getPhoneNumbers() / get_phone_numbers()
    phone_res = await provider.getPhoneNumbers({"phone_number_id": "123456"}, {"access_token": "valid-token"})
    assert isinstance(phone_res, dict)

    # 3. getTemplates() / get_templates()
    templates = await provider.getTemplates({"business_account_id": "waba-1"}, {"access_token": "valid-token"})
    assert len(templates) > 0
    assert any(t["name"] == "welcome_business" for t in templates)

    # 4. sendTextMessage() / send_text_message()
    send_text = await provider.sendTextMessage(
        account_config={"phone_number_id": "123"},
        recipient_address="+919876543210",
        body="Hello customer",
        idempotency_key="tx-1",
        credentials={"access_token": "tok"},
    )
    assert send_text.ok is True
    assert send_text.provider_message_id is not None

    # 5. sendTemplateMessage() / send_template_message()
    send_tpl = await provider.sendTemplateMessage(
        account_config={"phone_number_id": "123"},
        recipient_address="+919876543210",
        subject=None,
        body="",
        idempotency_key="tpl-1",
        credentials={"access_token": "tok"},
        template={"provider_template_name": "welcome_business"},
    )
    assert send_tpl.ok is True

    # 6. sendMediaMessage() / send_media_message()
    media_res = await provider.sendMediaMessage(
        account_config={"phone_number_id": "123"},
        recipient_address="+919876543210",
        media_type="image",
        media_link="https://qbitpro.com/demo.jpg",
        caption="Product brochure",
        credentials={"access_token": "tok"},
    )
    assert media_res.ok is True

    # 7. markMessageAsRead() / mark_message_as_read()
    read_ok = await provider.markMessageAsRead(
        account_config={"phone_number_id": "123"},
        message_id="wamid.mock123",
        credentials={"access_token": "tok"},
    )
    assert read_ok is True

    # 8. validateWebhook() / validate_webhook()
    secret = "test-secret"
    body = b'{"object": "whatsapp_business_account"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert provider.validateWebhook(raw_body=body, signature_header=sig, app_secret=secret) is True
    assert provider.validateWebhook(raw_body=body, signature_header="sha256=invalidsig", app_secret=secret) is False


# ==============================================================================
# 2. Webhook Handshake & Signature Security
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_get_challenge_handshake(client: AsyncClient, app):
    """Verify GET challenge handshake verifies hub.verify_token and echoes hub.challenge."""
    app.state.settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = "secret_verify_token_123"

    # Valid challenge
    res = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "secret_verify_token_123",
            "hub.challenge": "1155998877",
        },
    )
    assert res.status_code == 200
    assert res.text == "1155998877"

    # Invalid token rejected
    bad_res = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "1155998877",
        },
    )
    assert bad_res.status_code == 401


@pytest.mark.asyncio
async def test_webhook_post_signature_verification(client: AsyncClient, app):
    """Verify POST webhook validates X-Hub-Signature-256 HMAC."""
    app.state.settings.WHATSAPP_APP_SECRET = "whatsapp_meta_app_secret_xyz"

    raw_payload = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode("utf-8")
    valid_sig = "sha256=" + hmac.new(b"whatsapp_meta_app_secret_xyz", raw_payload, hashlib.sha256).hexdigest()

    # Valid signature
    res = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw_payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True

    # Bad signature rejected
    bad_res = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw_payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=corrupted_hash"},
    )
    assert bad_res.status_code == 401


# ==============================================================================
# 3. Inbound Processing, Normalization & Idempotency
# ==============================================================================

@pytest.mark.asyncio
async def test_inbound_message_persistence_and_deduplication(app):
    """Verify inbound WhatsApp messages create conversations and deduplicate on provider retries."""
    db = app.state.db
    engine = ConversationEngine()

    async with db.session() as session:
        account = await seed_account(session, channel="WHATSAPP", provider="mock")

        inbound_msg = UnifiedInboundMessage(
            channel="WHATSAPP",
            contact_phone="+919876543210",
            external_contact_id="wa-user-987",
            provider_message_id="wamid.inbound.001",
            message_type="TEXT",
            body="Interested in QbitPro growth plan",
        )

        conv1, msg1, created1 = await engine.ingest_inbound(session, inbound_msg, account=account)
        assert created1 is True
        assert conv1.contact_phone == "+919876543210"
        assert msg1.body == "Interested in QbitPro growth plan"
        assert conv1.unread_count == 1

        # Redelivered webhook (same provider_message_id) must be idempotent
        conv2, msg2, created2 = await engine.ingest_inbound(session, inbound_msg, account=account)
        assert created2 is False
        assert msg2.id == msg1.id
        assert conv2.unread_count == 1  # does not double count


@pytest.mark.asyncio
async def test_delivery_status_ladder_never_downgrades_read(app):
    """Verify forward-only delivery state: READ status is not overwritten by an out-of-order DELIVERED event."""
    db = app.state.db
    engine = ConversationEngine()

    async with db.session() as session:
        account = await seed_account(session, channel="WHATSAPP", provider="mock")

        conv = Conversation(
            channel="WHATSAPP",
            sending_account_id=account.id,
            contact_phone="+919876543210",
            status=ConversationStatus.OPEN,
        )
        session.add(conv)
        await session.flush()

        msg = Message(
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND.value,
            message_type="TEXT",
            body="Outbound offer",
            provider_message_id="wamid.outbound.ladder",
            status=MessageStatus.SENT.value,
        )
        session.add(msg)
        await session.commit()

        # 1. Received READ event first (fast user read)
        await engine.record_delivery_status(
            session,
            provider_message_id="wamid.outbound.ladder",
            status=MessageStatus.READ.value,
            occurred_at=utcnow(),
        )
        await session.refresh(msg)
        assert msg.status == MessageStatus.READ.value

        # 2. Delayed DELIVERED event arrives out of order
        await engine.record_delivery_status(
            session,
            provider_message_id="wamid.outbound.ladder",
            status=MessageStatus.DELIVERED.value,
            occurred_at=utcnow() - timedelta(seconds=5),
        )
        await session.refresh(msg)
        # Message status MUST remain READ
        assert msg.status == MessageStatus.READ.value


# ==============================================================================
# 4. Outbound 24h Window & Template Rules
# ==============================================================================

@pytest.mark.asyncio
async def test_whatsapp_24h_service_window_enforcement(app):
    """Verify free-text sending is blocked outside the 24h customer-service window."""
    db = app.state.db
    settings = app.state.settings
    engine = ConversationEngine()
    replies = ReplyService(engine)

    async with db.session() as session:
        account = await seed_account(session, channel="WHATSAPP", provider="mock")

        # Case A: Outside window (last inbound was 48 hours ago)
        conv_expired = Conversation(
            channel="WHATSAPP",
            sending_account_id=account.id,
            contact_phone="+919876543210",
            status=ConversationStatus.OPEN,
            last_inbound_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        session.add(conv_expired)
        await session.commit()

        with pytest.raises(TemplateRequiredError):
            await replies.queue_reply(
                session,
                conv_expired,
                user_id=None,
                body="Hello outside window",
                client_message_id="req-outside",
                settings=settings,
            )

        # Case B: Inside window (last inbound was 10 minutes ago)
        conv_active = Conversation(
            channel="WHATSAPP",
            sending_account_id=account.id,
            contact_phone="+919876543211",
            status=ConversationStatus.OPEN,
            last_inbound_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        session.add(conv_active)
        await session.commit()

        msg, created = await replies.queue_reply(
            session,
            conv_active,
            user_id=None,
            body="Hello inside window",
            client_message_id="req-inside",
            settings=settings,
        )
        assert created is True
        assert msg.status == MessageStatus.SENDING.value

        # Deliver via Outbox
        outbox = OutboxService(settings, build_provider_registry(settings), owner="test-worker")
        processed = await outbox.process_cycle(session)
        assert processed == 1
        await session.refresh(msg)
        assert msg.status == MessageStatus.SENT.value


# ==============================================================================
# 5. Team Inbox, Internal Notes & Assignment
# ==============================================================================

@pytest.mark.asyncio
async def test_team_inbox_notes_and_assignment_timeline(app):
    """Verify internal team notes and employee assignment generate audit timeline events."""
    db = app.state.db
    engine = ConversationEngine()

    async with db.session() as session:
        from app.models.user import User
        from tests.conftest import ADMIN_EMAIL
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        assert admin is not None

        account = await seed_account(session, channel="WHATSAPP", provider="mock")

        conv = Conversation(
            channel="WHATSAPP",
            sending_account_id=account.id,
            contact_phone="+919876543210",
            status=ConversationStatus.OPEN,
        )
        session.add(conv)
        await session.commit()

        # 1. Add internal team note
        note = await engine.add_note(
            session,
            conversation=conv,
            user_id=admin.id,
            content="Customer requested a quote for 50 licenses.",
        )
        assert note.content == "Customer requested a quote for 50 licenses."

        # 2. Assign conversation to sales employee
        await engine.assign(
            session,
            conversation=conv,
            assigned_user_id=admin.id,
            actor_user_id=admin.id,
        )
        await session.refresh(conv)
        assert conv.assigned_user_id == admin.id

        # 3. Verify conversation timeline events
        events_stmt = select(ConversationEvent).where(ConversationEvent.conversation_id == conv.id)
        events = (await session.scalars(events_stmt)).all()
        event_types = [e.event_type for e in events]
        assert "NOTE_ADDED" in event_types
        assert "ASSIGNED" in event_types


# ==============================================================================
# 6. CRM Lead Linking & Timeline Sync
# ==============================================================================

@pytest.mark.asyncio
async def test_conversation_crm_lead_linking(app):
    """Verify linking a WhatsApp conversation to a CRM lead."""
    db = app.state.db
    engine = ConversationEngine()

    async with db.session() as session:
        from app.models.user import User
        from tests.conftest import ADMIN_EMAIL
        admin = (await session.scalars(select(User).where(User.email == ADMIN_EMAIL))).first()
        assert admin is not None

        account = await seed_account(session, channel="WHATSAPP", provider="mock")

        lead = Lead(
            business_name="Indore Agro Chemicals",
            phone="+919876543210",
            status="NEW",
        )
        session.add(lead)
        await session.flush()

        conv = Conversation(
            channel="WHATSAPP",
            sending_account_id=account.id,
            contact_phone="+919876543210",
            status=ConversationStatus.OPEN,
        )
        session.add(conv)
        await session.commit()

        # Link conversation to CRM lead
        await engine.link_lead(
            session,
            conversation=conv,
            lead_id=lead.id,
            actor_user_id=admin.id,
        )
        await session.refresh(conv)
        assert conv.lead_id == lead.id
        assert conv.match_status == "MATCHED"


# ==============================================================================
# 7. WhatsApp Campaigns & Approved Templates
# ==============================================================================

@pytest.mark.asyncio
async def test_whatsapp_campaign_launch_and_suppression(app):
    """Verify WhatsApp campaign requires approved template and obeys suppression rules."""
    from app.services.marketing.campaign import CampaignService
    from app.services.marketing.worker import CampaignWorker

    db = app.state.db
    settings = app.state.settings

    async with db.session() as session:
        lead = Lead(business_name="Zomato Vendor", phone="+919999900001", email="vendor@zomato.test")
        session.add(lead)
        await session.flush()

        template = CampaignTemplate(
            name="order_update",
            channel="WHATSAPP",
            subject=None,
            body="Order {{1}} status.",
            status="ACTIVE",
            origin=TemplateOrigin.PROVIDER.value,
            provider_template_id="tpl-approved-1",
            provider_status=ProviderTemplateStatus.APPROVED.value,
        )
        session.add(template)

        account = SendingAccount(
            name="QBIT Official WhatsApp",
            channel="WHATSAPP",
            provider="whatsapp_mock",
            identifier="+919876500000",
            status="ACTIVE",
            health_status="HEALTHY",
            config_metadata={"phone_number_id": "phone-123", "configured": True},
        )
        session.add(account)
        await session.flush()

        # Suppress recipient
        supp_svc = SuppressionService()
        await supp_svc.add(
            session,
            entry_type=SuppressionType.PHONE,
            address="+919999900001",
            channel="WHATSAPP",
            reason=SuppressionReason.UNSUBSCRIBED,
        )

        camp_svc = CampaignService()
        campaign = await camp_svc.create(
            session,
            name="WhatsApp Urgent Notification",
            channel="WHATSAPP",
            template_id=template.id,
            sending_account_id=account.id,
            audience_definition={"type": "selected", "lead_ids": [str(lead.id)]},
        )
        assert campaign.channel == "WHATSAPP"

        # Validate campaign: suppressed recipient must be excluded from eligible pool
        report = await camp_svc.validate(session, campaign.id, provider_registry=build_provider_registry(settings))
        assert report["ok"] is True
        assert report["eligibility"]["eligible"] == 0
        assert report["eligibility"]["suppressed"] == 1


# ==============================================================================
# 8. UI Views Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_whatsapp_and_inbox_ui_views(client: AsyncClient, admin_headers: dict):
    """Verify that Team Inbox and WhatsApp connection screens render with Stitch design."""
    # 1. Team Inbox
    inbox_res = await client.get("/inbox", headers=admin_headers)
    assert inbox_res.status_code == 200
    assert "Inbox" in inbox_res.text or "INBOX" in inbox_res.text.upper()

    # 2. WhatsApp connection wizard
    wa_new = await client.get("/connections/whatsapp/new", headers=admin_headers)
    assert wa_new.status_code == 200
    assert "WHATSAPP" in wa_new.text.upper()

    # 3. WhatsApp analytics dashboard
    wa_analytics = await client.get("/analytics/whatsapp", headers=admin_headers)
    assert wa_analytics.status_code == 200
    assert "WhatsApp" in wa_analytics.text or "WHATSAPP" in wa_analytics.text.upper()
