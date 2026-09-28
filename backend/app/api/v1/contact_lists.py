"""Contact Lists API routes (Phase 6 / Brief §15)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuditDep, DbSession, require_permission
from app.core.errors import ValidationError
from app.schemas.contact_lists import (
    AddMembersRequest,
    ContactListCreate,
    ContactListUpdate,
    CSVImportRequest,
)
from app.services.marketing.lists import ContactListService

router = APIRouter(prefix="/campaigns/lists", tags=["contact-lists"])
list_service = ContactListService()


async def _ctx(session: AsyncSession, user):
    from app.services import authorization as authz
    from app.services import rbac as rbac_service

    perms = await rbac_service.load_user_permissions(session, user.id)
    return await authz.resolve_context(session, user, perms)


@router.get("")
async def list_contact_lists(
    session: DbSession,
    user=Depends(require_permission("campaigns.view")),
):
    ctx = await _ctx(session, user)
    lists = await list_service.list_lists(session, organization_id=ctx.organization_id)
    return {"success": True, "data": lists}


@router.post("")
async def create_contact_list(
    payload: ContactListCreate,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.create")),
):
    ctx = await _ctx(session, user)
    cl = await list_service.create_list(
        session,
        name=payload.name,
        description=payload.description,
        organization_id=ctx.organization_id,
        user_id=user.id,
    )
    await audit.log(
        session,
        action="contact_list.created",
        resource_type="contact_list",
        resource_id=str(cl.id),
        actor_user_id=user.id,
        metadata={"name": cl.name},
    )
    return {"success": True, "data": cl.to_public_dict()}


@router.get("/{list_id}")
async def get_contact_list(
    list_id: uuid.UUID,
    session: DbSession,
    user=Depends(require_permission("campaigns.view")),
):
    ctx = await _ctx(session, user)
    cl = await list_service.get_list(session, list_id, organization_id=ctx.organization_id)
    return {"success": True, "data": cl.to_public_dict()}


@router.patch("/{list_id}")
async def update_contact_list(
    list_id: uuid.UUID,
    payload: ContactListUpdate,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.edit")),
):
    ctx = await _ctx(session, user)
    cl = await list_service.update_list(
        session,
        list_id,
        name=payload.name,
        description=payload.description,
        organization_id=ctx.organization_id,
    )
    await audit.log(
        session,
        action="contact_list.updated",
        resource_type="contact_list",
        resource_id=str(cl.id),
        actor_user_id=user.id,
        metadata={"name": cl.name},
    )
    return {"success": True, "data": cl.to_public_dict()}


@router.delete("/{list_id}")
async def delete_contact_list(
    list_id: uuid.UUID,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.delete")),
):
    ctx = await _ctx(session, user)
    await list_service.delete_list(session, list_id, organization_id=ctx.organization_id)
    await audit.log(
        session,
        action="contact_list.deleted",
        resource_type="contact_list",
        resource_id=str(list_id),
        actor_user_id=user.id,
    )
    return {"success": True, "data": {"deleted": True}}


@router.get("/{list_id}/members")
async def list_contact_list_members(
    list_id: uuid.UUID,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(require_permission("campaigns.view")),
):
    ctx = await _ctx(session, user)
    items, total = await list_service.list_members(
        session, list_id, organization_id=ctx.organization_id, limit=limit, offset=offset
    )
    return {"success": True, "data": {"items": items, "total": total, "limit": limit, "offset": offset}}


@router.post("/{list_id}/members")
async def add_contact_list_members(
    list_id: uuid.UUID,
    payload: AddMembersRequest,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.edit")),
):
    ctx = await _ctx(session, user)
    lead_uuids = []
    for raw in payload.lead_ids:
        try:
            lead_uuids.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"Invalid lead UUID: {raw!r}") from exc

    added, skipped_duplicates = await list_service.add_members(
        session,
        list_id,
        lead_uuids,
        organization_id=ctx.organization_id,
        user_id=user.id,
    )
    await audit.log(
        session,
        action="contact_list.members_added",
        resource_type="contact_list",
        resource_id=str(list_id),
        actor_user_id=user.id,
        metadata={"added": added, "skipped_duplicates": skipped_duplicates},
    )
    return {"success": True, "data": {"added": added, "skipped_duplicates": skipped_duplicates}}


@router.delete("/{list_id}/members/{lead_id}")
async def remove_contact_list_member(
    list_id: uuid.UUID,
    lead_id: uuid.UUID,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.edit")),
):
    ctx = await _ctx(session, user)
    removed = await list_service.remove_member(
        session, list_id, lead_id, organization_id=ctx.organization_id
    )
    await audit.log(
        session,
        action="contact_list.member_removed",
        resource_type="contact_list",
        resource_id=str(list_id),
        actor_user_id=user.id,
        metadata={"lead_id": str(lead_id)},
    )
    return {"success": True, "data": {"removed": removed}}


@router.get("/{list_id}/eligibility")
async def get_contact_list_eligibility(
    list_id: uuid.UUID,
    session: DbSession,
    channel: str = Query(default="EMAIL"),
    user=Depends(require_permission("campaigns.view")),
):
    ctx = await _ctx(session, user)
    stats = await list_service.get_eligibility_metrics(
        session, list_id, channel_name=channel, organization_id=ctx.organization_id
    )
    return {"success": True, "data": stats}


@router.post("/{list_id}/import-csv")
async def import_csv_to_contact_list(
    list_id: uuid.UUID,
    payload: CSVImportRequest,
    session: DbSession,
    audit: AuditDep,
    user=Depends(require_permission("campaigns.edit")),
):
    ctx = await _ctx(session, user)
    result = await list_service.import_csv_to_list(
        session,
        list_id,
        payload.csv_content,
        has_consent=payload.has_consent,
        consent_source=payload.consent_source,
        organization_id=ctx.organization_id,
        user_id=user.id,
    )
    await audit.log(
        session,
        action="contact_list.csv_imported",
        resource_type="contact_list",
        resource_id=str(list_id),
        actor_user_id=user.id,
        metadata=result,
    )
    return {"success": True, "data": result}
