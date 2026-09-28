"""CRM lead contacts and interactions service (Phase 5)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.lead import LeadContact
from app.models.scrape import Lead
from app.services.leads.activity import LeadActivityService

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactService:
    def __init__(self, activities: LeadActivityService | None = None):
        self.activities = activities or LeadActivityService()

    async def add_contact(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
        *,
        first_name: str,
        last_name: str | None = None,
        title: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        is_primary: bool = False,
        is_verified: bool = False,
        created_by: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> LeadContact:
        fname = (first_name or "").strip()
        if not fname:
            raise ValidationError("Contact first name is required")

        lead = await session.get(Lead, lead_id)
        if not lead:
            raise NotFoundError("Lead not found")

        email_clean = email.strip().lower() if email else None
        if email_clean and not _EMAIL_RE.match(email_clean):
            raise ValidationError(f"Invalid contact email address: {email}")

        phone_clean = phone.strip() if phone else None
        org_id = organization_id or lead.organization_id

        # If primary, unset existing primary contacts for this lead
        if is_primary:
            existing_res = await session.execute(
                select(LeadContact).where(LeadContact.lead_id == lead_id, LeadContact.is_primary.is_(True))
            )
            for c in existing_res.scalars().all():
                c.is_primary = False

        contact = LeadContact(
            lead_id=lead.id,
            organization_id=org_id,
            first_name=fname,
            last_name=last_name.strip() if last_name else None,
            title=title.strip() if title else None,
            email=email_clean,
            phone=phone_clean,
            is_primary=is_primary,
            is_verified=is_verified,
            created_by=created_by,
        )
        session.add(contact)

        now = datetime.now(timezone.utc)
        lead.last_activity_at = now
        lead.updated_at = now

        full_name = f"{fname} {last_name.strip()}" if last_name else fname
        await self.activities.log(
            session,
            lead.id,
            "contact_added",
            message=f"Added contact person: {full_name}" + (f" ({title})" if title else ""),
            metadata={"contact_id": str(contact.id), "name": full_name, "email": email_clean},
            user_id=created_by,
            commit=False,
        )

        if commit:
            await session.commit()
            await session.refresh(contact)

        return contact

    async def list_contacts(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
    ) -> list[LeadContact]:
        res = await session.execute(
            select(LeadContact)
            .where(LeadContact.lead_id == lead_id)
            .order_by(LeadContact.is_primary.desc(), LeadContact.created_at.asc())
        )
        return list(res.scalars().all())

    async def log_interaction(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
        *,
        interaction_type: str,
        notes: str,
        user_id: uuid.UUID | None = None,
        contact_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> dict:
        """Log a real communication interaction (Call, Email, Meeting, WhatsApp) on the lead timeline."""
        lead = await session.get(Lead, lead_id)
        if not lead:
            raise NotFoundError("Lead not found")

        type_clean = (interaction_type or "COMMUNICATION").upper()
        notes_clean = (notes or "").strip()
        if not notes_clean:
            raise ValidationError("Interaction notes are required")

        now = datetime.now(timezone.utc)
        lead.last_activity_at = now
        lead.updated_at = now

        activity = await self.activities.log(
            session,
            lead.id,
            "interaction_logged",
            message=f"[{type_clean}] {notes_clean}",
            metadata={
                "interaction_type": type_clean,
                "notes": notes_clean,
                "contact_id": str(contact_id) if contact_id else None,
            },
            user_id=user_id,
            commit=False,
        )

        if commit:
            await session.commit()

        return {
            "id": str(activity.id),
            "lead_id": str(lead.id),
            "interaction_type": type_clean,
            "notes": notes_clean,
            "logged_at": now.isoformat(),
        }
