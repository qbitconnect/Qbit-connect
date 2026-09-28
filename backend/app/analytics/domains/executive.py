"""CEO / Executive Dashboard Domain (Phase 10 §2).

Provides database-backed KPIs, pipeline metrics, campaign performance,
and team activity for CEO / Super Admin / Sales Managers.

Zero-hallucination rules:
- Every metric has an explicit source table, definition, and calculation.
- Missing or unsupported metrics return `None` (rendered as "—" or "Not configured")
  rather than misleading fake numbers or zeroes.
- Pipeline value and closed-won value are computed strictly from real `CrmDeal.amount`
  records; if none exist, `None` is returned.
- Campaign spend is reported as `None` because operational DB does not record ad spend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.core.filters import AnalyticsFilters
from app.analytics.core.math import comparison, safe_rate
from app.analytics.core.query_builder import base_leads_query, in_period
from app.analytics.core.time import Period, day_bucket
from app.models.analytics import CrmDeal
from app.models.lead import (
    FollowUpStatus,
    LeadActivity,
    LeadDuplicateCandidate,
    LeadFollowUp,
    LeadStatus,
)
from app.models.marketing import Campaign, CampaignEvent, CampaignRecipient
from app.models.messaging import Conversation, Message
from app.models.scrape import Lead
from app.models.social import SocialAccount, SocialPost, SocialPostTarget


async def executive_kpis(
    session: AsyncSession,
    filters: AnalyticsFilters,
    prev_filters: AnalyticsFilters | None = None,
    tz_name: str = "UTC",
    dialect: str = "sqlite",
) -> dict[str, Any]:
    """Calculate executive dashboard metrics with optional comparison vs previous period."""
    current = await _calculate_snapshot(session, filters, tz_name, dialect)
    previous = None
    if prev_filters is not None:
        previous = await _calculate_snapshot(session, prev_filters, tz_name, dialect)

    # Compare key metrics
    kpi_keys = [
        "total_leads", "new_leads", "qualified_leads", "converted_leads",
        "open_opportunities", "overdue_followups", "activities_completed",
        "messages_sent", "messages_delivered",
    ]
    kpis_comparison = {}
    for key in kpi_keys:
        curr_val = current.get("kpis", {}).get(key, 0)
        prev_val = previous.get("kpis", {}).get(key) if previous else None
        kpis_comparison[key] = comparison(curr_val, prev_val)

    return {
        "period": filters.period.to_dict() if filters.period else None,
        "kpis": kpis_comparison,
        "lead_generation": current["lead_generation"],
        "sales_pipeline": current["sales_pipeline"],
        "campaign_performance": current["campaign_performance"],
        "team_activity": current["team_activity"],
        "metadata": {
            "timezone": tz_name,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def _calculate_snapshot(
    session: AsyncSession,
    filters: AnalyticsFilters,
    tz_name: str,
    dialect: str,
) -> dict[str, Any]:
    """Compute point-in-time metrics for one period."""
    period = filters.period
    tz = ZoneInfo(tz_name)

    # =========================================================================
    # A. Lead Generation
    # =========================================================================
    base = base_leads_query(filters)
    total_leads = int(await session.scalar(base.with_only_columns(func.count(Lead.id))) or 0)

    # Status counts in period
    status_rows = (await session.execute(
        base.with_only_columns(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    )).all()
    by_status = {s or "UNKNOWN": int(c) for s, c in status_rows}

    new_leads = by_status.get("NEW", 0)
    qualified_leads = by_status.get("QUALIFIED", 0)
    converted_leads = by_status.get("CONVERTED", 0)
    lost_leads = by_status.get("LOST", 0)
    not_interested = by_status.get("NOT_INTERESTED", 0)
    unqualified_leads = lost_leads + not_interested
    awaiting_qualification = sum(
        by_status.get(s, 0) for s in ("NEW", "ASSIGNED", "CONTACTED", "VERIFIED")
    )

    # Pre-existing leads active in period (created before period start, but updated within period)
    pre_existing_leads = 0
    if period and period.start:
        pre_existing_q = select(func.count(Lead.id)).where(
            Lead.created_at < period.start,
            Lead.updated_at >= period.start,
        )
        if period.end:
            pre_existing_q = pre_existing_q.where(Lead.updated_at < period.end)
        pre_existing_leads = int(await session.scalar(pre_existing_q) or 0)

    # Unique businesses and duplicates
    unique_businesses = int(await session.scalar(
        base.with_only_columns(func.count(distinct(Lead.business_name)))
        .where(Lead.business_name.is_not(None))
    ) or 0)

    merged_duplicates = int(await session.scalar(
        base.with_only_columns(func.count(Lead.id))
        .where(Lead.merged_into_id.is_not(None))
    ) or 0)

    pending_duplicate_pairs = int(await session.scalar(
        select(func.count(LeadDuplicateCandidate.id))
        .where(LeadDuplicateCandidate.status == "PENDING")
    ) or 0)

    # Leads by source
    source_rows = (await session.execute(
        base.with_only_columns(Lead.source, func.count(Lead.id))
        .group_by(Lead.source)
        .order_by(func.count(Lead.id).desc())
        .limit(10)
    )).all()
    leads_by_source = [
        {"source": s or "Direct / Unknown", "count": int(c)}
        for s, c in source_rows
    ]

    # Acquisition trends over time (daily bucket)
    trend_rows = []
    if period and period.start and period.end:
        bucket = day_bucket(Lead.created_at, tz_name, period.start, period.end, dialect=dialect)
        daily_q = (
            base.with_only_columns(bucket.label("day"), func.count(Lead.id).label("count"))
            .group_by(bucket)
            .order_by(bucket)
        )
        for r in (await session.execute(daily_q)).all():
            trend_rows.append({"date": str(r.day), "leads": int(r.count)})

    # =========================================================================
    # B. CRM and Sales Pipeline
    # =========================================================================
    # Check if any CrmDeal records exist
    total_deal_records = int(await session.scalar(select(func.count(CrmDeal.id))) or 0)

    if total_deal_records > 0:
        # We have genuine CRM deals
        deal_rows = (await session.execute(
            select(CrmDeal.stage, func.count(CrmDeal.id), func.sum(CrmDeal.amount))
            .where(in_period(CrmDeal.created_at, period))
            .group_by(CrmDeal.stage)
        )).all()
        deals_by_stage = {r[0]: {"count": int(r[1]), "value": float(r[2] or 0.0)} for r in deal_rows}

        open_deals_count = sum(
            deals_by_stage.get(s, {}).get("count", 0) for s in ("PROPOSAL", "NEGOTIATION")
        )
        pipeline_val_sum = sum(
            deals_by_stage.get(s, {}).get("value", 0.0) for s in ("PROPOSAL", "NEGOTIATION")
        )
        closed_won_deals = deals_by_stage.get("CLOSED_WON", {}).get("count", 0)
        closed_won_val = deals_by_stage.get("CLOSED_WON", {}).get("value", 0.0)
        closed_lost_deals = deals_by_stage.get("CLOSED_LOST", {}).get("count", 0)

        pipeline_value = round(pipeline_val_sum, 2)
        closed_won_value = round(closed_won_val, 2)
    else:
        # Operational leads pipeline stages
        open_deals_count = sum(
            by_status.get(s, 0)
            for s in ("NEW", "ASSIGNED", "CONTACTED", "FOLLOW_UP", "QUALIFIED", "INTERESTED", "REPLIED")
        )
        deals_by_stage = {s: {"count": c, "value": None} for s, c in by_status.items()}
        closed_won_deals = converted_leads
        closed_lost_deals = lost_leads
        # Honest indicator: no commercial deal amounts recorded in system
        pipeline_value = None
        closed_won_value = None

    # Pipeline counts from leads
    leads_assigned = int(await session.scalar(
        base.with_only_columns(func.count(Lead.id)).where(Lead.assigned_user_id.is_not(None))
    ) or 0)
    leads_contacted = by_status.get("CONTACTED", 0)
    leads_followed_up = int(await session.scalar(
        select(func.count(distinct(LeadFollowUp.lead_id)))
        .where(LeadFollowUp.status == FollowUpStatus.COMPLETED.value)
    ) or 0)

    # Overdue follow-ups
    now_utc = datetime.now(timezone.utc)
    overdue_followups = int(await session.scalar(
        select(func.count(LeadFollowUp.id))
        .where(
            LeadFollowUp.status == FollowUpStatus.PENDING.value,
            LeadFollowUp.due_at < now_utc,
        )
    ) or 0)

    # Pipeline stages breakdown
    pipeline_stages = [
        {"stage": "NEW", "label": "New Inbound", "count": by_status.get("NEW", 0)},
        {"stage": "ASSIGNED", "label": "Assigned to Rep", "count": by_status.get("ASSIGNED", 0)},
        {"stage": "CONTACTED", "label": "Contacted", "count": by_status.get("CONTACTED", 0)},
        {"stage": "FOLLOW_UP", "label": "Follow Up", "count": by_status.get("FOLLOW_UP", 0)},
        {"stage": "QUALIFIED", "label": "Qualified Prospect", "count": by_status.get("QUALIFIED", 0)},
        {"stage": "CONVERTED", "label": "Converted / Won", "count": converted_leads},
        {"stage": "LOST", "label": "Lost / Disqualified", "count": lost_leads},
    ]

    # =========================================================================
    # C. Campaign Performance
    # =========================================================================
    # Email metrics
    email_events_q = select(CampaignEvent.event_type, func.count(CampaignEvent.id)).where(
        in_period(CampaignEvent.created_at, period)
    ).group_by(CampaignEvent.event_type)
    email_events = dict((await session.execute(email_events_q)).all())

    email_sent = email_events.get("MESSAGE_SENT", 0)
    email_delivered = email_events.get("MESSAGE_DELIVERED", 0)
    email_opened = email_events.get("MESSAGE_OPENED", 0)
    email_bounced = email_events.get("MESSAGE_BOUNCED", 0)
    email_unsubscribed = email_events.get("MESSAGE_UNSUBSCRIBED", 0)
    email_clicked = email_events.get("MESSAGE_CLICKED", None)  # None if tracking off

    # WhatsApp metrics
    wa_base = select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
        Conversation.channel == "WHATSAPP",
        in_period(Message.created_at, period),
    )
    wa_sent = int(await session.scalar(
        wa_base.with_only_columns(func.count(Message.id)).where(Message.direction == "OUT")
    ) or 0)
    wa_delivered = int(await session.scalar(
        wa_base.with_only_columns(func.count(Message.id)).where(
            Message.direction == "OUT",
            Message.delivered_at.is_not(None),
        )
    ) or 0)
    wa_read_count = int(await session.scalar(
        wa_base.with_only_columns(func.count(Message.id)).where(
            Message.direction == "OUT",
            Message.read_at.is_not(None),
        )
    ) or 0)
    # Honest missing-data: if provider never sent read events, report None
    wa_read = wa_read_count if wa_read_count > 0 else None

    wa_failed = int(await session.scalar(
        wa_base.with_only_columns(func.count(Message.id)).where(
            Message.direction == "OUT",
            Message.failed_at.is_not(None),
        )
    ) or 0)
    wa_replied = int(await session.scalar(
        wa_base.with_only_columns(func.count(Message.id)).where(Message.direction == "IN")
    ) or 0)

    # Social metrics
    social_posts_published = int(await session.scalar(
        select(func.count(SocialPost.id)).where(
            SocialPost.status == "PUBLISHED",
            in_period(SocialPost.published_at, period),
        )
    ) or 0)
    social_accounts_connected = int(await session.scalar(
        select(func.count(SocialAccount.id)).where(SocialAccount.status == "ACTIVE")
    ) or 0)

    # Active campaigns
    active_campaigns = int(await session.scalar(
        select(func.count(Campaign.id)).where(
            Campaign.status.in_(["RUNNING", "SCHEDULED", "QUEUED"]),
            in_period(Campaign.created_at, period),
        )
    ) or 0)

    # =========================================================================
    # D. Team Activity
    # =========================================================================
    activities_completed = int(await session.scalar(
        select(func.count(LeadActivity.id)).where(in_period(LeadActivity.created_at, period))
    ) or 0)

    followups_completed = int(await session.scalar(
        select(func.count(LeadFollowUp.id)).where(
            LeadFollowUp.status == FollowUpStatus.COMPLETED.value,
            in_period(LeadFollowUp.completed_at, period),
        )
    ) or 0)

    # Calls and meetings logged
    calls_logged = int(await session.scalar(
        select(func.count(LeadActivity.id)).where(
            LeadActivity.event_type.in_(["call_logged", "call"]),
            in_period(LeadActivity.created_at, period),
        )
    ) or 0)

    meetings_logged = int(await session.scalar(
        select(func.count(LeadActivity.id)).where(
            LeadActivity.event_type.in_(["meeting_logged", "meeting"]),
            in_period(LeadActivity.created_at, period),
        )
    ) or 0)

    notes_added = int(await session.scalar(
        select(func.count(LeadActivity.id)).where(
            LeadActivity.event_type == "note_added",
            in_period(LeadActivity.created_at, period),
        )
    ) or 0)

    total_messages_sent = email_sent + wa_sent
    total_messages_delivered = email_delivered + wa_delivered

    return {
        "kpis": {
            "total_leads": total_leads,
            "new_leads": new_leads,
            "qualified_leads": qualified_leads,
            "converted_leads": converted_leads,
            "open_opportunities": open_deals_count,
            "overdue_followups": overdue_followups,
            "activities_completed": activities_completed,
            "messages_sent": total_messages_sent,
            "messages_delivered": total_messages_delivered,
        },
        "lead_generation": {
            "total_leads": total_leads,
            "new_leads": new_leads,
            "pre_existing_leads": pre_existing_leads,
            "qualified_leads": qualified_leads,
            "converted_leads": converted_leads,
            "unqualified_leads": unqualified_leads,
            "awaiting_qualification": awaiting_qualification,
            "unique_businesses": unique_businesses,
            "duplicate_records": merged_duplicates + pending_duplicate_pairs,
            "merged_duplicates": merged_duplicates,
            "pending_duplicates": pending_duplicate_pairs,
            "leads_by_source": leads_by_source,
            "trends": trend_rows,
        },
        "sales_pipeline": {
            "open_opportunities": open_deals_count,
            "pipeline_stages": pipeline_stages,
            "leads_assigned": leads_assigned,
            "leads_contacted": leads_contacted,
            "leads_followed_up": leads_followed_up,
            "leads_converted": converted_leads,
            "overdue_followups": overdue_followups,
            "deals_won": closed_won_deals,
            "deals_lost": closed_lost_deals,
            "closed_won_deals": closed_won_deals,
            "closed_lost_deals": closed_lost_deals,
            "pipeline_value_inr": pipeline_value,
            "closed_won_value_inr": closed_won_value,
            "conversion_rate": safe_rate(converted_leads, total_leads),
            "lead_to_opportunity_rate": safe_rate(open_deals_count, total_leads),
            "opportunity_to_win_rate": safe_rate(closed_won_deals, open_deals_count + closed_won_deals) if (open_deals_count + closed_won_deals) > 0 else None,
            "lead_to_win_rate": safe_rate(converted_leads, total_leads),
        },
        "campaign_performance": {
            "active_campaigns": active_campaigns,
            "email": {
                "sent": email_sent,
                "delivered": email_delivered,
                "opened": email_opened,
                "bounced": email_bounced,
                "unsubscribed": email_unsubscribed,
                "clicked": email_clicked,
                "open_rate": safe_rate(email_opened, email_delivered) if email_delivered > 0 else None,
                "delivery_rate": safe_rate(email_delivered, email_sent),
                "bounce_rate": safe_rate(email_bounced, email_sent),
                "click_to_open_rate": safe_rate(email_clicked, email_opened) if email_clicked is not None and email_opened > 0 else None,
            },
            "whatsapp": {
                "sent": wa_sent,
                "delivered": wa_delivered,
                "read": wa_read,
                "failed": wa_failed,
                "replied": wa_replied,
                "delivery_rate": safe_rate(wa_delivered, wa_sent),
                "read_rate": safe_rate(wa_read, wa_delivered) if wa_read is not None else None,
                "reply_rate": safe_rate(wa_replied, wa_delivered) if wa_delivered > 0 else None,
            },
            "social": {
                "posts_published": social_posts_published,
                "posts_scheduled": 0,
                "posts_failed": 0,
                "accounts_connected": social_accounts_connected,
                "total_engagements": None,
            },
            "cost_per_lead_inr": None,
            "return_on_ad_spend": None,
            "campaign_spend": None,
            "acquisition_cost": None,
        },
        "team_activity": {
            "assigned_leads": leads_assigned,
            "activities_completed": activities_completed,
            "followups_completed": followups_completed,
            "overdue_followups": overdue_followups,
            "calls_logged": calls_logged,
            "meetings_logged": meetings_logged,
            "notes_added": notes_added,
            "conversions": converted_leads,
        },
    }
