"""Analytics API (Phase 10 §24).

All routes are JWT-authenticated and enforce granular `analytics.*`
permissions SERVER-SIDE. Analytics is read-only against operational data;
the only mutating endpoints are the admin aggregate-rebuild and cache-clear
actions (both `analytics.manage`, both audit-logged — spec §26, §27).

Responses never contain provider credentials or secrets (spec §32): the
domain modules query operational tables only and never touch the credential
vault, connection secrets or token storage.
"""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.analytics.aggregation import AggregationService, run_diagnostics
from app.analytics.core.cache import AnalyticsCache
from app.analytics.core.exceptions import AnalyticsValidationError
from app.analytics.core.filters import FILTER_KEYS
from app.analytics.core.time import PERIOD_PRESETS
from app.analytics.reports.executive_exporter import (
    export_employees_csv,
    export_executive_csv,
    export_executive_xlsx,
)
from app.models.analytics import CrmDeal, PerformanceTarget
from app.models.enterprise import Team, TeamMember
from app.analytics.service import AnalyticsRequest, AnalyticsService
from app.api.deps import AuditDep, DbSession, require_permission
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])

_TEAM_PERMISSION = "analytics.view_team"


def _service(request: Request) -> AnalyticsService:
    return AnalyticsService(request.app.state.redis)


#: non-filter query params accepted on analytics endpoints
_RESERVED_PARAMS = {"period", "date_from", "date_to", "timezone", "compare", "sort", "report_type", "format", "team_id"}


def _request_params(
    request: Request,
    *,
    period: str | None,
    date_from: str | None,
    date_to: str | None,
    timezone: str | None,
    compare: bool,
    scope: str = "global",
) -> AnalyticsRequest:
    """Pull allowlisted filters from query params. Unknown query params are
    REJECTED (never silently ignored — explicit is safer, spec §32)."""
    filter_params: dict[str, list[str]] = {}
    for key in request.query_params.keys():
        if key in _RESERVED_PARAMS:
            continue
        if key not in FILTER_KEYS:
            raise AnalyticsValidationError(f"Unknown filter(s): {key}")
        filter_params[key] = request.query_params.getlist(key)
    return AnalyticsRequest(
        period=period, date_from=date_from, date_to=date_to,
        timezone=timezone, compare=compare,
        filter_params=filter_params, scope=scope,
    )


_PERIOD_DESC = " | ".join(PERIOD_PRESETS) + " | custom (with date_from/date_to)"


# ----------------------------------------------------------------- overview
@router.get("/overview")
async def overview(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None, description=_PERIOD_DESC),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("analytics.view")),
):
    data = await _service(request).overview(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=compare),
    )
    return {"success": True, "data": data}


# -------------------------------------------------------------------- leads
@router.get("/leads")
async def leads_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("analytics.view_leads")),
):
    data = await _service(request).leads(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=compare),
    )
    return {"success": True, "data": data}


@router.get("/leads/sources")
async def lead_sources(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    sort: str = Query(default="leads", pattern=r"^(leads|quality|qualification|conversion)$"),
    user: User = Depends(require_permission("analytics.view_leads")),
):
    data = await _service(request).leads_sources(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False),
        sort=sort,
    )
    return {"success": True, "data": data}


@router.get("/leads/funnel")
async def lead_funnel(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_leads")),
):
    data = await _service(request).leads_funnel(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False),
    )
    return {"success": True, "data": data}


# ----------------------------------------------------------------- scraping
@router.get("/scraping")
async def scraping_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("analytics.view_scraping")),
):
    data = await _service(request).scraping(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=compare),
    )
    return {"success": True, "data": data}


@router.get("/scraping/scrapers")
async def scraping_scrapers(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_scraping")),
):
    params = _request_params(request, period=period, date_from=date_from,
                             date_to=date_to, timezone=timezone, compare=False)
    data = await _service(request).scraping(session, params)
    return {"success": True, "data": {"scrapers": data.get("scrapers", []),
                                      "period": data.get("period")}}


# ---------------------------------------------------------------- marketing
@router.get("/marketing")
async def marketing_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("analytics.view_marketing")),
):
    data = await _service(request).marketing(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=compare),
    )
    return {"success": True, "data": data}


@router.get("/campaigns/{campaign_id}")
async def campaign_analytics(
    request: Request,
    session: DbSession,
    campaign_id: str,
    user: User = Depends(require_permission("campaigns.analytics")),
):
    """Campaign detail reuses the existing Phase 5/7 analytics service so the
    campaign page and this endpoint can never disagree (spec §10, §28)."""
    import uuid as uuid_module

    try:
        campaign_uuid = uuid_module.UUID(campaign_id)
    except ValueError as exc:
        raise AnalyticsValidationError("campaign_id must be a UUID") from exc
    data = await _service(request).campaign_detail(session, campaign_uuid)
    if data is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("Campaign not found")
    return {"success": True, "data": data}


# ------------------------------------------------------- whatsapp and email
@router.get("/whatsapp")
async def whatsapp_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_whatsapp")),
):
    data = await _service(request).whatsapp(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False),
    )
    return {"success": True, "data": data}


@router.get("/email")
async def email_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_email")),
):
    data = await _service(request).email(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False),
    )
    return {"success": True, "data": data}


# -------------------------------------------------------------------- inbox
@router.get("/inbox")
async def inbox_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("analytics.view_inbox")),
):
    data = await _service(request).inbox(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=compare),
    )
    return {"success": True, "data": data}


# --------------------------------------------------------------------- team
@router.get("/team")
async def team_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission(_TEAM_PERMISSION)),
):
    """Team analytics scoped by the established visibility rules (spec §14):
    ASSIGNED_ONLY deployments see only the requesting user's own activity."""
    from sqlalchemy import select as sa_select

    from app.models.user import User as UserModel

    settings = request.app.state.settings
    visibility = getattr(settings, "QBIT_INBOX_VISIBILITY", "ALL")
    if visibility == "ASSIGNED_ONLY":
        allowed = [user.id]
    else:
        rows = (await session.execute(sa_select(UserModel.id))).scalars().all()
        allowed = list(rows)
    data = await _service(request).team(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False,
                                 scope=str(user.id)),
        allowed_user_ids=allowed,
    )
    return {"success": True, "data": data}


# --------------------------------------------------------------- automation
@router.get("/automation")
async def automation_analytics(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_automation")),
):
    data = await _service(request).automation(
        session, _request_params(request, period=period, date_from=date_from,
                                 date_to=date_to, timezone=timezone, compare=False),
    )
    return {"success": True, "data": data}


# ----------------------------------------------------- admin: aggregates etc
@router.get("/aggregates/status")
async def aggregates_status(
    request: Request,
    session: DbSession,
    user: User = Depends(require_permission("analytics.manage")),
):
    service = AggregationService(dialect=session.bind.dialect.name
                                 if session.bind is not None else "sqlite")
    return {"success": True, "data": {"recent_runs": await service.recent_runs(session)}}


class RebuildIn(BaseModel):
    domains: list[str] | None = None
    day_start: date | None = None
    day_end: date | None = None


@router.post("/aggregates/rebuild")
async def aggregates_rebuild(
    request: Request,
    session: DbSession,
    body: RebuildIn,
    audit: AuditDep,
    user: User = Depends(require_permission("analytics.manage")),
):
    """Manual aggregate rebuild for administrators (spec §21). NEVER deletes
    operational data — only derived aggregate rows are recomputed (§39)."""
    from datetime import datetime, timedelta, timezone as dt_timezone

    service = AggregationService(dialect=session.bind.dialect.name
                                 if session.bind is not None else "sqlite")
    day_end = body.day_end or (datetime.now(dt_timezone.utc).date() - timedelta(days=1))
    day_start = body.day_start or (day_end - timedelta(days=29))
    try:
        results = await service.rebuild_range(
            session, day_start, day_end, domains=body.domains, triggered_by="MANUAL",
        )
    except ValueError as exc:
        raise AnalyticsValidationError(str(exc)) from exc
    await audit.log(
        session, action="analytics.aggregate_rebuilt", actor_user_id=user.id,
        resource_type="analytics_aggregates",
        metadata={"day_start": day_start.isoformat(), "day_end": day_end.isoformat(),
                  "domains": body.domains},
    )
    return {"success": True, "data": {"results": results}}


@router.post("/cache/clear")
async def cache_clear(
    request: Request,
    session: DbSession,
    audit: AuditDep,
    user: User = Depends(require_permission("analytics.manage")),
):
    cache = AnalyticsCache(request.app.state.redis)
    removed = await cache.clear_all()
    await audit.log(
        session, action="analytics.cache_cleared", actor_user_id=user.id,
        resource_type="analytics_cache", metadata={"entries_removed": removed},
    )
    return {"success": True, "data": {"entries_removed": removed}}


@router.get("/diagnostics")
async def diagnostics(
    request: Request,
    session: DbSession,
    user: User = Depends(require_permission("analytics.manage")),
):
    """Data-quality checks (spec §29): flag anomalies, never modify data."""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    return {"success": True, "data": await run_diagnostics(session, dialect)}


# =============================================================================
# Phase 10: CEO Executive Dashboard, Employee Performance & Targets
# =============================================================================

@router.get("/executive", summary="CEO / Executive dashboard KPIs and pipeline")
async def executive_dashboard(
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None, description=_PERIOD_DESC),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    compare: bool = Query(default=False),
    user: User = Depends(require_permission("executive.view")),
):
    """Real database-backed executive KPI breakdown across Lead Gen, CRM Pipeline,
    Campaigns, and Team Activity. Zero hallucinated metrics."""
    data = await _service(request).executive(
        session, _request_params(
            request, period=period, date_from=date_from, date_to=date_to,
            timezone=timezone, compare=compare, scope=str(user.id),
        ),
    )
    return {"success": True, "data": data}


@router.get("/employees", summary="Employee performance directory")
async def list_employees(
    request: Request,
    session: DbSession,
    team_id: uuid.UUID | None = Query(default=None),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_team")),
):
    """Employee reporting directory with assigned leads, contacted, qualified,
    converted, follow-ups, and transparent conversion rates."""
    # Scope check: ordinary operators only see their own profile
    allowed = None
    settings = request.app.state.settings
    visibility = getattr(settings, "QBIT_INBOX_VISIBILITY", "ALL")
    user_roles = set(user.role_codes)
    if "OPERATOR" in user_roles and not (user_roles & {"ADMIN", "SUPER_ADMIN", "CEO", "MANAGER"}):
        allowed = [user.id]
    elif visibility == "ASSIGNED_ONLY":
        allowed = [user.id]

    data = await _service(request).employees_performance(
        session, _request_params(
            request, period=period, date_from=date_from, date_to=date_to,
            timezone=timezone, compare=False, scope=str(user.id),
        ),
        allowed_user_ids=allowed,
        team_id=team_id,
    )
    return {"success": True, "data": data}


@router.get("/employees/{user_id}", summary="Employee performance detail")
async def employee_detail(
    user_id: uuid.UUID,
    request: Request,
    session: DbSession,
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_team")),
):
    """Detailed employee performance breakdown, activity trends, and targets tracking."""
    user_roles = set(user.role_codes)
    is_admin = bool(user_roles & {"ADMIN", "SUPER_ADMIN", "CEO", "MANAGER"})
    if not is_admin and user.id != user_id:
        raise HTTPException(status_code=403, detail="Access denied: Cannot view other employee's private performance")

    data = await _service(request).employee_detail(
        session, user_id, _request_params(
            request, period=period, date_from=date_from, date_to=date_to,
            timezone=timezone, compare=False, scope=str(user_id),
        ),
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"success": True, "data": data}


@router.get("/teams", summary="Team-wise performance and workload distribution")
async def teams_performance(
    request: Request,
    session: DbSession,
    team_id: uuid.UUID | None = Query(default=None),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("analytics.view_team")),
):
    """Team-wise performance, lead volume, and workload distribution across members."""
    data = await _service(request).teams_performance(
        session, _request_params(
            request, period=period, date_from=date_from, date_to=date_to,
            timezone=timezone, compare=False, scope=str(user.id),
        ),
        team_id=team_id,
    )
    return {"success": True, "data": data}


# ------------------------------------------------ Performance Targets CRUD
class TargetCreateIn(BaseModel):
    user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    metric: str = Field(..., description="leads_assigned|leads_contacted|leads_qualified|conversions|follow_ups_completed|calls_logged|deals_won")
    target_value: float = Field(..., gt=0)
    period_type: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly|custom)$")
    start_date: date
    end_date: date
    notes: str | None = Field(default=None, max_length=1000)


async def _user_org_id(session: DbSession, user: User) -> uuid.UUID | None:
    """Resolve active organization id for user from canonical membership context."""
    from app.services import authorization as authz
    from app.services import rbac as rbac_service
    perms = getattr(user, "_qbit_perms", None)
    if perms is None:
        perms = await rbac_service.load_user_permissions(session, user.id)
    ctx = await authz.resolve_context(session, user, perms)
    return ctx.organization_id if ctx else None


@router.get("/targets", summary="List performance targets")
async def list_targets(
    session: DbSession,
    user_id: uuid.UUID | None = Query(default=None),
    team_id: uuid.UUID | None = Query(default=None),
    user: User = Depends(require_permission("targets.view")),
):
    """List configured targets for employees or teams."""
    user_roles = set(user.role_codes)
    is_admin = bool(user_roles & {"ADMIN", "SUPER_ADMIN", "CEO", "MANAGER"})
    is_super = bool(user_roles & {"SUPER_ADMIN", "CEO"})
    user_org = await _user_org_id(session, user)

    q = select(PerformanceTarget).order_by(desc(PerformanceTarget.created_at))
    if not is_super and user_org:
        from sqlalchemy import or_
        q = q.where(or_(PerformanceTarget.organization_id == user_org, PerformanceTarget.organization_id.is_(None)))

    if not is_admin:
        # Non-managers can only see their own targets
        q = q.where(PerformanceTarget.user_id == user.id)
    else:
        if user_id:
            q = q.where(PerformanceTarget.user_id == user_id)
        if team_id:
            q = q.where(PerformanceTarget.team_id == team_id)

    targets = (await session.execute(q)).scalars().all()
    return {"success": True, "data": [t.to_dict() for t in targets]}


@router.post("/targets", summary="Create or update performance target")
async def create_target(
    body: TargetCreateIn,
    audit: AuditDep,
    session: DbSession,
    user: User = Depends(require_permission("targets.manage")),
):
    """Set an explicit performance target for an employee or team."""
    user_org = await _user_org_id(session, user)
    now = datetime.now(dt_timezone.utc)
    target = PerformanceTarget(
        organization_id=user_org,
        user_id=body.user_id,
        team_id=body.team_id,
        metric=body.metric,
        target_value=body.target_value,
        period_type=body.period_type,
        start_date=body.start_date,
        end_date=body.end_date,
        notes=body.notes,
        set_by=user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)

    await audit.log(
        session, action="targets.target_created", actor_user_id=user.id,
        resource_type="performance_target", resource_id=str(target.id),
        metadata={
            "metric": target.metric,
            "target_value": target.target_value,
            "user_id": str(target.user_id) if target.user_id else None,
            "team_id": str(target.team_id) if target.team_id else None,
        },
    )
    return {"success": True, "data": target.to_dict()}


@router.delete("/targets/{target_id}", summary="Delete performance target")
async def delete_target(
    target_id: uuid.UUID,
    audit: AuditDep,
    session: DbSession,
    user: User = Depends(require_permission("targets.manage")),
):
    """Remove a configured performance target."""
    target = await session.get(PerformanceTarget, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    user_roles = set(user.role_codes)
    is_super = bool(user_roles & {"SUPER_ADMIN", "CEO"})
    user_org = await _user_org_id(session, user)
    if not is_super and user_org and target.organization_id and target.organization_id != user_org:
        raise HTTPException(status_code=404, detail="Target not found")

    await session.delete(target)
    await session.commit()

    await audit.log(
        session, action="targets.target_deleted", actor_user_id=user.id,
        resource_type="performance_target", resource_id=str(target_id),
        metadata={"metric": target.metric},
    )
    return {"success": True, "data": {"id": str(target_id), "deleted": True}}


# ------------------------------------------------ CRM Commercial Deals CRUD
class DealCreateIn(BaseModel):
    lead_id: uuid.UUID | None = None
    title: str = Field(..., min_length=1, max_length=200)
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", max_length=10)
    stage: str = Field(default="PROPOSAL", pattern="^(PROPOSAL|NEGOTIATION|CLOSED_WON|CLOSED_LOST)$")
    probability: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_close_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/deals", summary="List CRM commercial deals")
async def list_deals(
    session: DbSession,
    lead_id: uuid.UUID | None = Query(default=None),
    stage: str | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    user: User = Depends(require_permission("deals.view")),
):
    """List commercial deals recorded for genuine sales pipeline calculation."""
    user_roles = set(user.role_codes)
    is_super = bool(user_roles & {"SUPER_ADMIN", "CEO"})
    user_org = await _user_org_id(session, user)

    q = select(CrmDeal).order_by(desc(CrmDeal.created_at))
    if not is_super and user_org:
        from sqlalchemy import or_
        q = q.where(or_(CrmDeal.organization_id == user_org, CrmDeal.organization_id.is_(None)))
    if lead_id:
        q = q.where(CrmDeal.lead_id == lead_id)
    if stage:
        q = q.where(CrmDeal.stage == stage.upper())
    if owner_id:
        q = q.where(CrmDeal.owner_id == owner_id)

    deals = (await session.execute(q)).scalars().all()
    return {"success": True, "data": [d.to_dict() for d in deals]}


@router.post("/deals", summary="Create CRM commercial deal")
async def create_deal(
    body: DealCreateIn,
    audit: AuditDep,
    session: DbSession,
    user: User = Depends(require_permission("deals.manage")),
):
    """Create a commercial deal with real amount for pipeline value tracking."""
    user_roles = set(user.role_codes)
    is_super = bool(user_roles & {"SUPER_ADMIN", "CEO"})
    user_org = await _user_org_id(session, user)

    if body.lead_id is not None:
        from app.models.scrape import Lead
        lead = await session.get(Lead, body.lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        if not is_super and user_org and lead.organization_id and lead.organization_id != user_org:
            raise HTTPException(status_code=404, detail="Lead not found")

    now = datetime.now(dt_timezone.utc)
    deal = CrmDeal(
        organization_id=user_org,
        lead_id=body.lead_id,
        title=body.title,
        amount=body.amount,
        currency=body.currency,
        stage=body.stage,
        probability=body.probability,
        expected_close_date=body.expected_close_date,
        closed_at=now if body.stage in ("CLOSED_WON", "CLOSED_LOST") else None,
        owner_id=user.id,
        created_by=user.id,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )
    session.add(deal)
    await session.commit()
    await session.refresh(deal)

    await audit.log(
        session, action="deals.deal_created", actor_user_id=user.id,
        resource_type="crm_deal", resource_id=str(deal.id),
        metadata={"title": deal.title, "amount": deal.amount, "stage": deal.stage},
    )
    return {"success": True, "data": deal.to_dict()}


# ------------------------------------------------ Data Exports
@router.get("/export", summary="Export dashboard or performance report (CSV/XLSX)")
async def export_report(
    request: Request,
    session: DbSession,
    audit: AuditDep,
    report_type: str = Query(..., pattern="^(executive|employees|teams)$"),
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    timezone: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("reports.export")),
):
    """Authorized CSV/XLSX export with CSV formula injection protection and audit logging."""
    req = _request_params(
        request, period=period, date_from=date_from, date_to=date_to,
        timezone=timezone, compare=False, scope=str(user.id),
    )
    today_str = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")

    if report_type == "executive":
        exec_data = await _service(request).executive(session, req)
        if format == "xlsx":
            content = export_executive_xlsx(exec_data)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"qbit-executive-report-{today_str}.xlsx"
        else:
            content = export_executive_csv(exec_data).encode("utf-8")
            media_type = "text/csv; charset=utf-8"
            filename = f"qbit-executive-report-{today_str}.csv"

    elif report_type == "employees":
        emp_data = await _service(request).employees_performance(session, req)
        employees = emp_data.get("employees", [])
        period_label = req.period or f"{req.date_from or ''} to {req.date_to or ''}"
        csv_text = export_employees_csv(employees, period_label=period_label)
        content = csv_text.encode("utf-8")
        media_type = "text/csv; charset=utf-8"
        filename = f"qbit-employee-performance-{today_str}.csv"
    else:
        teams_data = await _service(request).teams_performance(session, req)
        content = str(teams_data).encode("utf-8")
        media_type = "application/json"
        filename = f"qbit-teams-performance-{today_str}.json"

    await audit.log(
        session, action="reports.exported", actor_user_id=user.id,
        resource_type="report_export",
        metadata={"report_type": report_type, "format": format, "filename": filename},
    )

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

