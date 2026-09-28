"""Phase 10 — Performance Targets & Business Reporting.

NON-DESTRUCTIVE:
- creates performance_targets table for configurable employee & team targets
- creates crm_deals table for reliable commercial deal records and pipeline/won values

Revision ID: 0018_performance_targets_and_reporting
Revises: 0017_ai_agents_intelligence
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_performance_targets_and_reporting"
down_revision = "0017_ai_agents_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- performance_targets --------------------------------------------------
    op.create_table(
        "performance_targets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("period_type", sa.String(length=20), nullable=False, server_default="monthly"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("set_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_targets_org_user", "performance_targets", ["organization_id", "user_id"])
    op.create_index("ix_targets_org_team", "performance_targets", ["organization_id", "team_id"])
    op.create_index("ix_targets_metric_period", "performance_targets", ["metric", "start_date", "end_date"])
    op.create_index("ix_targets_user", "performance_targets", ["user_id"])
    op.create_index("ix_targets_team", "performance_targets", ["team_id"])

    # --- crm_deals ------------------------------------------------------------
    op.create_table(
        "crm_deals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("stage", sa.String(length=50), nullable=False, server_default="PROPOSAL"),
        sa.Column("probability", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("expected_close_date", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crm_deals_org", "crm_deals", ["organization_id"])
    op.create_index("ix_crm_deals_lead", "crm_deals", ["lead_id"])
    op.create_index("ix_crm_deals_stage", "crm_deals", ["stage"])
    op.create_index("ix_crm_deals_owner", "crm_deals", ["owner_id"])


def downgrade() -> None:
    op.drop_table("crm_deals")
    op.drop_table("performance_targets")
