"""Phase 6 — Marketing Contact Lists and Memberships.

NON-DESTRUCTIVE:
- creates contact_lists table for mailing list management
- creates contact_list_members table linking CRM leads/contacts to lists
- creates associated indexes and unique constraints

Revision ID: 0015_marketing_contact_lists
Revises: 0014_crm_pipeline_followups
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_marketing_contact_lists"
down_revision = "0014_crm_pipeline_followups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- contact_lists --------------------------------------------------------
    op.create_table(
        "contact_lists",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contact_lists_org", "contact_lists", ["organization_id"])
    op.create_index("ix_contact_lists_name", "contact_lists", ["name"])
    op.create_index("ix_contact_lists_created", "contact_lists", ["created_at"])

    # --- contact_list_members -------------------------------------------------
    op.create_table(
        "contact_list_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "list_id",
            sa.Uuid(),
            sa.ForeignKey("contact_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.Uuid(),
            sa.ForeignKey("lead_contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("list_id", "lead_id", name="uq_contact_list_member_lead"),
    )
    op.create_index("ix_contact_list_members_list", "contact_list_members", ["list_id"])
    op.create_index("ix_contact_list_members_lead", "contact_list_members", ["lead_id"])
    op.create_index("ix_contact_list_members_contact", "contact_list_members", ["contact_id"])


def downgrade() -> None:
    op.drop_table("contact_list_members")
    op.drop_table("contact_lists")
