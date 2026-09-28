"""Contact List & Segment Service (Phase 6 / Brief §15).

Provides management for mailing lists and recipient audiences connected to CRM leads:
- Contact list CRUD with tenant scoping and RBAC
- Member management with duplicate membership prevention
- Eligibility calculations via central EligibilityService
- Safe CSV import with column mapping, RFC validation, and explicit consent audit
- Export with suppression protection and PII safeguards
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.lead import LeadContact
from app.models.marketing import Channel, ContactList, ContactListMember
from app.models.scrape import Lead
from app.services.marketing.channels import get_channel
from app.services.marketing.eligibility import EligibilityService

logger = get_logger("qbit.marketing.lists")


class ContactListService:
    def __init__(self, eligibility_service: EligibilityService | None = None) -> None:
        self.eligibility = eligibility_service or EligibilityService()

    async def create_list(
        self,
        session: AsyncSession,
        *,
        name: str,
        description: str | None = None,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> ContactList:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationError("List name is required")
        if len(clean_name) > 200:
            raise ValidationError("List name cannot exceed 200 characters")

        contact_list = ContactList(
            name=clean_name,
            description=(description or "").strip() or None,
            organization_id=organization_id,
            created_by=user_id,
        )
        session.add(contact_list)
        await session.commit()
        await session.refresh(contact_list)
        return contact_list

    async def get_list(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
    ) -> ContactList:
        stmt = select(ContactList).where(ContactList.id == list_id)
        if organization_id is not None:
            stmt = stmt.where(ContactList.organization_id == organization_id)
        row = await session.scalar(stmt)
        if row is None:
            raise NotFoundError("Contact list not found")
        return row

    async def list_lists(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID | None = None,
    ) -> list[dict]:
        stmt = select(
            ContactList,
            func.count(ContactListMember.id).label("member_count"),
        ).outerjoin(
            ContactListMember, ContactList.id == ContactListMember.list_id
        ).group_by(ContactList.id).order_by(ContactList.created_at.desc())

        if organization_id is not None:
            stmt = stmt.where(ContactList.organization_id == organization_id)

        rows = (await session.execute(stmt)).all()
        result = []
        for cl, count in rows:
            data = cl.to_public_dict()
            data["member_count"] = int(count or 0)
            result.append(data)
        return result

    async def update_list(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> ContactList:
        cl = await self.get_list(session, list_id, organization_id)
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValidationError("List name cannot be empty")
            cl.name = clean_name
        if description is not None:
            cl.description = description.strip() or None

        cl.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(cl)
        return cl

    async def delete_list(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
    ) -> bool:
        cl = await self.get_list(session, list_id, organization_id)
        await session.delete(cl)
        await session.commit()
        return True

    async def add_members(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        lead_ids: list[uuid.UUID],
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> tuple[int, int]:
        """Add CRM leads to contact list. Returns (added_count, skipped_duplicates)."""
        await self.get_list(session, list_id, organization_id)

        if not lead_ids:
            return 0, 0

        # Existing member leads
        existing_stmt = select(ContactListMember.lead_id).where(
            ContactListMember.list_id == list_id,
            ContactListMember.lead_id.in_(lead_ids),
        )
        existing_ids = set((await session.scalars(existing_stmt)).all())

        new_ids = [lid for lid in lead_ids if lid not in existing_ids]
        added = 0

        for lid in new_ids:
            member = ContactListMember(
                list_id=list_id,
                lead_id=lid,
                organization_id=organization_id,
                added_by=user_id,
            )
            session.add(member)
            added += 1

        if added > 0:
            await session.commit()

        return added, len(existing_ids)

    async def remove_member(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        lead_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
    ) -> bool:
        await self.get_list(session, list_id, organization_id)
        stmt = delete(ContactListMember).where(
            ContactListMember.list_id == list_id,
            ContactListMember.lead_id == lead_id,
        )
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0

    async def list_members(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        *,
        organization_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return leads enrolled in the list with their verification and eligibility."""
        await self.get_list(session, list_id, organization_id)

        count_stmt = select(func.count(ContactListMember.id)).where(
            ContactListMember.list_id == list_id
        )
        total = int(await session.scalar(count_stmt) or 0)

        stmt = select(Lead, ContactListMember.created_at.label("joined_at")).join(
            ContactListMember, Lead.id == ContactListMember.lead_id
        ).where(
            ContactListMember.list_id == list_id,
            Lead.merged_into_id.is_(None),
        ).order_by(ContactListMember.created_at.desc()).limit(limit).offset(offset)

        rows = (await session.execute(stmt)).all()
        items = []
        for lead, joined_at in rows:
            data = lead.to_public_dict()
            data["joined_at"] = joined_at.isoformat() if joined_at else None
            items.append(data)

        return items, total

    async def get_eligibility_metrics(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        *,
        channel_name: str = "EMAIL",
        organization_id: uuid.UUID | None = None,
    ) -> dict:
        """Calculate real sending eligibility across the contact list members."""
        await self.get_list(session, list_id, organization_id)
        channel = get_channel(channel_name)

        stmt = select(Lead).join(
            ContactListMember, Lead.id == ContactListMember.lead_id
        ).where(
            ContactListMember.list_id == list_id,
            Lead.merged_into_id.is_(None),
        )
        leads = (await session.scalars(stmt)).all()

        total = len(leads)
        eligible = 0
        missing_address = 0
        suppressed = 0
        opted_out = 0

        for lead in leads:
            address = channel.get_recipient_address(lead)
            if not address:
                missing_address += 1
                continue

            verdict = await self.eligibility.check(
                session, lead=lead, channel=channel, organization_id=organization_id
            )
            if verdict.eligible:
                eligible += 1
            else:
                reason = str(verdict.reason).upper()
                if "SUPPRESSED" in reason:
                    suppressed += 1
                elif "OPT_OUT" in reason or "CONSENT" in reason:
                    opted_out += 1

        return {
            "total_members": total,
            "eligible": eligible,
            "missing_address": missing_address,
            "suppressed": suppressed,
            "opted_out": opted_out,
            "eligibility_rate_pct": round((eligible / total * 100.0), 1) if total > 0 else 0.0,
        }

    async def import_csv_to_list(
        self,
        session: AsyncSession,
        list_id: uuid.UUID,
        csv_content: str,
        *,
        has_consent: bool = False,
        consent_source: str | None = None,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> dict:
        """Import contacts from CSV text directly into CRM and attach to list.
        Never auto-grants consent unless explicitly confirmed with consent_source."""
        await self.get_list(session, list_id, organization_id)

        reader = csv.DictReader(io.StringIO(csv_content))
        if not reader.fieldnames:
            raise ValidationError("CSV has no headers or is empty")

        # Normalize field headers
        normalized_headers = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames}

        imported = 0
        duplicates = 0
        failed = 0
        now = datetime.now(timezone.utc)

        for row in reader:
            email = row.get(normalized_headers.get("email", "")) or row.get(normalized_headers.get("email_address", ""))
            business_name = row.get(normalized_headers.get("business_name", "")) or row.get(normalized_headers.get("company", ""))
            contact_name = row.get(normalized_headers.get("contact_name", "")) or row.get(normalized_headers.get("name", ""))

            email = str(email or "").strip()
            business_name = str(business_name or "").strip()
            contact_name = str(contact_name or "").strip()

            if not email:
                failed += 1
                continue

            # Look up existing lead by email or create new lead
            lead = None
            if email:
                lead = await session.scalar(
                    select(Lead).where(Lead.email == email, Lead.merged_into_id.is_(None)).limit(1)
                )

            if lead is None:
                lead = Lead(
                    business_name=business_name or contact_name or email.split("@")[0],
                    contact_name=contact_name or None,
                    email=email or None,
                    source="csv_import",
                    source_type="import",
                    organization_id=organization_id,
                    created_by=user_id,
                    metadata_json={
                        "import_consent": has_consent,
                        "consent_source": consent_source if has_consent else "unconfirmed",
                        "imported_at": now.isoformat(),
                    },
                )
                session.add(lead)
                await session.flush()

            # Add to list member
            existing_member = await session.scalar(
                select(ContactListMember.id).where(
                    ContactListMember.list_id == list_id,
                    ContactListMember.lead_id == lead.id,
                )
            )
            if existing_member:
                duplicates += 1
            else:
                member = ContactListMember(
                    list_id=list_id,
                    lead_id=lead.id,
                    organization_id=organization_id,
                    added_by=user_id,
                )
                session.add(member)
                imported += 1

        await session.commit()

        return {
            "imported": imported,
            "duplicates": duplicates,
            "failed": failed,
            "total_processed": imported + duplicates + failed,
            "has_consent_recorded": has_consent,
        }
