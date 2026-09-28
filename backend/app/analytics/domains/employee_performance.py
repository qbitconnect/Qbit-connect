"""Employee Performance & Targets Analytics Domain (Phase 10 §3).

Comprehensive, descriptive, evidence-based performance tracking for employees and teams.
Zero opaque AI scores; transparent calculation rules and auditable denominators.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.core.filters import AnalyticsFilters
from app.analytics.core.math import safe_rate
from app.analytics.core.query_builder import in_period
from app.analytics.core.time import Period, day_bucket
from app.models.analytics import CrmDeal, PerformanceTarget
from app.models.enterprise import Team, TeamMember
from app.models.lead import FollowUpStatus, LeadActivity, LeadFollowUp
from app.models.scrape import Lead
from app.models.user import User


async def list_employees_performance(
    session: AsyncSession,
    filters: AnalyticsFilters,
    allowed_user_ids: list[uuid.UUID] | None = None,
    team_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List employee directory with summary performance metrics in the given period."""
    q = select(User).where(User.is_active.is_(True))
    if allowed_user_ids is not None:
        q = q.where(User.id.in_(allowed_user_ids))

    if team_id:
        q = q.join(TeamMember, TeamMember.user_id == User.id).where(TeamMember.team_id == team_id)

    q = q.order_by(User.full_name, User.email)
    users = (await session.execute(q)).scalars().all()
    if not users:
        return []

    uids = [u.id for u in users]
    period = filters.period
    now_utc = datetime.now(timezone.utc)

    # 1. Assigned leads per user
    leads_q = select(Lead.assigned_user_id, Lead.status, func.count(Lead.id)).where(
        Lead.assigned_user_id.in_(uids),
        in_period(Lead.created_at, period),
    ).group_by(Lead.assigned_user_id, Lead.status)
    lead_rows = (await session.execute(leads_q)).all()

    lead_stats: dict[uuid.UUID, dict[str, int]] = {uid: {} for uid in uids}
    for uid, status, cnt in lead_rows:
        if uid in lead_stats:
            lead_stats[uid][status or "UNKNOWN"] = int(cnt)

    # 2. Completed follow-ups
    fu_done_q = select(LeadFollowUp.assigned_user_id, func.count(LeadFollowUp.id)).where(
        LeadFollowUp.assigned_user_id.in_(uids),
        LeadFollowUp.status == FollowUpStatus.COMPLETED.value,
        in_period(LeadFollowUp.completed_at, period),
    ).group_by(LeadFollowUp.assigned_user_id)
    fu_done_map = dict((await session.execute(fu_done_q)).all())

    # 3. Overdue follow-ups
    fu_overdue_q = select(LeadFollowUp.assigned_user_id, func.count(LeadFollowUp.id)).where(
        LeadFollowUp.assigned_user_id.in_(uids),
        LeadFollowUp.status == FollowUpStatus.PENDING.value,
        LeadFollowUp.due_at < now_utc,
    ).group_by(LeadFollowUp.assigned_user_id)
    fu_overdue_map = dict((await session.execute(fu_overdue_q)).all())

    # 4. Activities logged (calls, meetings, notes, status changes)
    act_q = select(LeadActivity.user_id, func.count(LeadActivity.id)).where(
        LeadActivity.user_id.in_(uids),
        in_period(LeadActivity.created_at, period),
    ).group_by(LeadActivity.user_id)
    act_map = dict((await session.execute(act_q)).all())

    # 5. Teams per user
    tm_rows = (await session.execute(
        select(TeamMember.user_id, Team.name)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id.in_(uids))
    )).all()
    user_teams: dict[uuid.UUID, list[str]] = {}
    for uid, tname in tm_rows:
        user_teams.setdefault(uid, []).append(tname)

    # Compile result
    out = []
    for u in users:
        stats = lead_stats.get(u.id, {})
        total_assigned = sum(stats.values())
        contacted = stats.get("CONTACTED", 0)
        qualified = stats.get("QUALIFIED", 0)
        converted = stats.get("CONVERTED", 0)
        pending = sum(stats.get(s, 0) for s in ("NEW", "ASSIGNED", "CONTACTED", "FOLLOW_UP"))

        conv_rate = safe_rate(converted, total_assigned)
        fu_done = int(fu_done_map.get(u.id, 0))
        fu_overdue = int(fu_overdue_map.get(u.id, 0))
        act_logged = int(act_map.get(u.id, 0))
        team_list = user_teams.get(u.id, [])
        primary_team = team_list[0] if team_list else None
        primary_role = u.role_codes[0] if u.role_codes else "OPERATOR"
        name = u.full_name or u.email.split("@")[0]

        out.append({
            "user_id": str(u.id),
            "name": name,
            "full_name": name,
            "email": u.email,
            "role": primary_role,
            "roles": u.role_codes,
            "team_name": primary_team,
            "teams": team_list,
            "status": u.effective_status,
            "assigned_leads": total_assigned,
            "contacted_leads": contacted,
            "qualified_leads": qualified,
            "converted_leads": converted,
            "pending_leads": pending,
            "follow_ups_completed": fu_done,
            "followups_completed": fu_done,
            "followups_overdue": fu_overdue,
            "activities_logged": act_logged,
            "conversion_rate": conv_rate,
            "active_deals_count": 0,
            "closed_won_value_inr": None,
            "metrics": {
                "assigned_leads": total_assigned,
                "contacted_leads": contacted,
                "qualified_leads": qualified,
                "converted_leads": converted,
                "pending_leads": pending,
                "followups_completed": fu_done,
                "followups_overdue": fu_overdue,
                "activities_logged": act_logged,
                "conversion_rate": conv_rate,
            },
        })
    return out


async def get_employee_detail_performance(
    session: AsyncSession,
    user_id: uuid.UUID,
    filters: AnalyticsFilters,
    tz_name: str = "UTC",
    dialect: str = "sqlite",
) -> dict[str, Any] | None:
    """Detailed performance view for a single employee."""
    user = await session.get(User, user_id)
    if not user:
        return None

    period = filters.period
    now_utc = datetime.now(timezone.utc)

    # 1. Lead statuses assigned to this user
    leads_q = select(Lead.status, func.count(Lead.id)).where(
        Lead.assigned_user_id == user_id,
        in_period(Lead.created_at, period),
    ).group_by(Lead.status)
    by_status = dict((await session.execute(leads_q)).all())

    total_assigned = sum(by_status.values())
    contacted = by_status.get("CONTACTED", 0)
    qualified = by_status.get("QUALIFIED", 0)
    converted = by_status.get("CONVERTED", 0)
    pending = sum(by_status.get(s, 0) for s in ("NEW", "ASSIGNED", "CONTACTED", "FOLLOW_UP"))

    # 2. Follow-ups
    fu_due = int(await session.scalar(
        select(func.count(LeadFollowUp.id)).where(
            LeadFollowUp.assigned_user_id == user_id,
            LeadFollowUp.status == FollowUpStatus.PENDING.value,
        )
    ) or 0)
    fu_completed = int(await session.scalar(
        select(func.count(LeadFollowUp.id)).where(
            LeadFollowUp.assigned_user_id == user_id,
            LeadFollowUp.status == FollowUpStatus.COMPLETED.value,
            in_period(LeadFollowUp.completed_at, period),
        )
    ) or 0)
    fu_overdue = int(await session.scalar(
        select(func.count(LeadFollowUp.id)).where(
            LeadFollowUp.assigned_user_id == user_id,
            LeadFollowUp.status == FollowUpStatus.PENDING.value,
            LeadFollowUp.due_at < now_utc,
        )
    ) or 0)

    # 3. Activity breakdown
    act_rows = (await session.execute(
        select(LeadActivity.event_type, func.count(LeadActivity.id)).where(
            LeadActivity.user_id == user_id,
            in_period(LeadActivity.created_at, period),
        ).group_by(LeadActivity.event_type)
    )).all()
    activities_by_type = {et: int(c) for et, c in act_rows}

    # 4. Activity trends over time (daily)
    activity_trends = []
    if period and period.start and period.end:
        bucket = day_bucket(LeadActivity.created_at, tz_name, period.start, period.end, dialect=dialect)
        daily_q = (
            select(bucket.label("day"), func.count(LeadActivity.id).label("count"))
            .where(LeadActivity.user_id == user_id, in_period(LeadActivity.created_at, period))
            .group_by(bucket)
            .order_by(bucket)
        )
        for r in (await session.execute(daily_q)).all():
            activity_trends.append({"date": str(r.day), "activities": int(r.count)})

    # 5. CRM Deals (if any)
    deal_won_count = int(await session.scalar(
        select(func.count(CrmDeal.id)).where(
            CrmDeal.owner_id == user_id,
            CrmDeal.stage == "CLOSED_WON",
            in_period(CrmDeal.closed_at, period),
        )
    ) or 0)
    deal_won_val = await session.scalar(
        select(func.sum(CrmDeal.amount)).where(
            CrmDeal.owner_id == user_id,
            CrmDeal.stage == "CLOSED_WON",
            in_period(CrmDeal.closed_at, period),
        )
    )

    # 6. Targets comparison
    target_date_from = period.start.date() if period and period.start else date.today()
    target_date_to = period.end.date() if period and period.end else date.today()

    targets_q = select(PerformanceTarget).where(
        PerformanceTarget.user_id == user_id,
        PerformanceTarget.start_date <= target_date_to,
        PerformanceTarget.end_date >= target_date_from,
    )
    targets = (await session.execute(targets_q)).scalars().all()

    target_results = []
    for t in targets:
        actual = 0
        if t.metric == "leads_assigned":
            actual = total_assigned
        elif t.metric == "leads_contacted":
            actual = contacted
        elif t.metric == "leads_qualified":
            actual = qualified
        elif t.metric == "conversions":
            actual = converted
        elif t.metric == "follow_ups_completed":
            actual = fu_completed
        elif t.metric == "calls_logged":
            actual = activities_by_type.get("call_logged", 0) + activities_by_type.get("call", 0)
        elif t.metric == "deals_won":
            actual = deal_won_count

        progress_pct = round((actual / t.target_value) * 100, 1) if t.target_value > 0 else 0.0
        gap = max(0.0, t.target_value - actual)
        target_results.append({
            "id": str(t.id),
            "metric": t.metric,
            "target_value": t.target_value,
            "actual_value": actual,
            "progress_pct": progress_pct,
            "gap": gap,
            "period_type": t.period_type,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "notes": t.notes,
        })

    return {
        "user": user.to_public_dict(),
        "summary": {
            "assigned_leads": total_assigned,
            "contacted_leads": contacted,
            "qualified_leads": qualified,
            "converted_leads": converted,
            "pending_leads": pending,
            "conversion_rate": safe_rate(converted, total_assigned),
            "followups_due": fu_due,
            "followups_completed": fu_completed,
            "followups_overdue": fu_overdue,
            "deals_won": deal_won_count if deal_won_count > 0 else converted,
            "closed_won_value_inr": round(float(deal_won_val), 2) if deal_won_val is not None else None,
        },
        "activities_by_type": activities_by_type,
        "activity_trends": activity_trends,
        "targets": target_results,
    }


async def team_workload_and_reporting(
    session: AsyncSession,
    filters: AnalyticsFilters,
    team_id: uuid.UUID | None = None,
    allowed_user_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Team-wise performance and workload distribution."""
    # List teams
    teams_q = select(Team).where(Team.is_active.is_(True))
    if team_id:
        teams_q = teams_q.where(Team.id == team_id)
    teams = (await session.execute(teams_q)).scalars().all()

    team_reports = []
    period = filters.period

    for team in teams:
        # Get members of this team
        m_q = (
            select(User)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team.id, User.is_active.is_(True))
        )
        if allowed_user_ids is not None:
            m_q = m_q.where(User.id.in_(allowed_user_ids))

        members = (await session.execute(m_q)).scalars().all()
        if not members:
            continue

        member_ids = [m.id for m in members]

        # Total leads assigned to team members
        leads_q = select(Lead.assigned_user_id, func.count(Lead.id)).where(
            Lead.assigned_user_id.in_(member_ids),
            in_period(Lead.created_at, period),
        ).group_by(Lead.assigned_user_id)
        leads_by_user = dict((await session.execute(leads_q)).all())
        total_team_leads = sum(leads_by_user.values())

        # Conversions by member
        conv_q = select(Lead.assigned_user_id, func.count(Lead.id)).where(
            Lead.assigned_user_id.in_(member_ids),
            Lead.status == "CONVERTED",
            in_period(Lead.created_at, period),
        ).group_by(Lead.assigned_user_id)
        conv_by_user = dict((await session.execute(conv_q)).all())
        total_conversions = sum(conv_by_user.values())

        # Workload distribution
        distribution = []
        for m in members:
            cnt = int(leads_by_user.get(m.id, 0))
            share = safe_rate(cnt, total_team_leads) if total_team_leads > 0 else 0.0
            distribution.append({
                "user_id": str(m.id),
                "name": m.full_name or m.email.split("@")[0],
                "email": m.email,
                "assigned_leads": cnt,
                "share_pct": round(share * 100, 1) if share is not None else 0.0,
                "conversions": int(conv_by_user.get(m.id, 0)),
            })

        team_reports.append({
            "team_id": str(team.id),
            "team_name": team.name,
            "description": team.description,
            "member_count": len(members),
            "members_count": len(members),
            "lead_count": total_team_leads,
            "total_assigned_leads": total_team_leads,
            "total_conversions": total_conversions,
            "conversion_rate": safe_rate(total_conversions, total_team_leads),
            "active_conversations": 0,
            "workload_distribution": distribution,
        })

    unassigned_leads = int(await session.scalar(
        select(func.count(Lead.id)).where(
            Lead.assigned_user_id.is_(None),
            in_period(Lead.created_at, period),
        )
    ) or 0)

    return {
        "period": period.to_dict() if period else None,
        "teams": team_reports,
        "unassigned_leads": unassigned_leads,
        "total_active_conversations": 0,
    }
