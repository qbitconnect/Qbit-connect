"""Phase 5 — CRM pipeline stages, follow-up tasks, lead status history, and contacts.

NON-DESTRUCTIVE:
- adds priority and last_activity_at columns to leads with default
- creates lead_status_history, lead_follow_ups, and lead_contacts tables
- creates associated indexes for high-performance CRM filtering

Revision ID: 0014_crm_pipeline_followups
Revises: 0013_actor_platform_storage
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_crm_pipeline_followups"
down_revision = "0013_actor_platform_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- leads: CRM additions ------------------------------------------------
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.String(length=20), nullable=False, server_default="MEDIUM")
        )
        batch_op.add_column(
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index("ix_leads_priority", ["priority"])
        batch_op.create_index("ix_leads_last_activity", ["last_activity_at"])

    # --- lead_status_history -------------------------------------------------
    op.create_table(
        "lead_status_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("previous_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_status_hist_lead", "lead_status_history", ["lead_id", "created_at"]
    )
    op.create_index("ix_lead_status_hist_user", "lead_status_history", ["changed_by"])
    op.create_index("ix_lead_status_hist_org", "lead_status_history", ["organization_id"])

    # --- lead_follow_ups -----------------------------------------------------
    op.create_table(
        "lead_follow_ups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="MEDIUM"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_follow_ups_lead", "lead_follow_ups", ["lead_id", "due_at"]
    )
    op.create_index(
        "ix_lead_follow_ups_assigned",
        "lead_follow_ups",
        ["assigned_user_id", "status", "due_at"],
    )
    op.create_index("ix_lead_follow_ups_org", "lead_follow_ups", ["organization_id"])

    # --- lead_contacts -------------------------------------------------------
    op.create_table(
        "lead_contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("first_name", sa.String(length=150), nullable=True),
        sa.Column("last_name", sa.String(length=150), nullable=True),
        sa.Column("title", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_contacts_lead", "lead_contacts", ["lead_id"])
    op.create_index("ix_lead_contacts_email", "lead_contacts", ["email"])
    op.create_index("ix_lead_contacts_phone", "lead_contacts", ["phone"])
    op.create_index("ix_lead_contacts_org", "lead_contacts", ["organization_id"])


def downgrade() -> None:
    op.drop_table("lead_contacts")
    op.drop_table("lead_follow_ups")
    op.drop_table("lead_status_history")
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_index("ix_leads_last_activity")
        batch_op.drop_index("ix_leads_priority")
        batch_op.drop_column("last_activity_at")
        batch_op.drop_column("priority")
