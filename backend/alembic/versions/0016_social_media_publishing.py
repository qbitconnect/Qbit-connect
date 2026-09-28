"""Phase 8 — Social Media Integration, Publishing, Scheduling & Analytics.

NON-DESTRUCTIVE:
- creates social_accounts table for authorized platform accounts (Facebook, IG, LinkedIn)
- creates social_posts table for post content, drafts, approvals, and schedules
- creates social_post_targets table for per-account dispatch, idempotency, and analytics
- creates social_audit_logs table for immutable lifecycle event recording
- creates associated indexes and unique constraints

Revision ID: 0016_social_media_publishing
Revises: 0015_marketing_contact_lists
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_social_media_publishing"
down_revision = "0015_marketing_contact_lists"
branch_labels = None
depends_on = None

PortableJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # --- social_accounts ------------------------------------------------------
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("account_type", sa.String(length=50), nullable=False, server_default="PAGE"),
        sa.Column("account_id", sa.String(length=255), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("profile_picture_url", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column("status_message", sa.String(length=500), nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("metadata_json", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("platform", "account_id", "organization_id", name="uq_social_platform_account"),
    )
    op.create_index("ix_social_accounts_org", "social_accounts", ["organization_id"])
    op.create_index("ix_social_accounts_platform", "social_accounts", ["platform"])
    op.create_index("ix_social_accounts_status", "social_accounts", ["status"])

    # --- social_posts ---------------------------------------------------------
    op.create_table(
        "social_posts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("platform_customizations", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("media_urls", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="DRAFT"),
        sa.Column("approval_status", sa.String(length=50), nullable=False, server_default="DRAFT"),
        sa.Column("approver_id", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False, server_default="UTC"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_social_posts_org", "social_posts", ["organization_id"])
    op.create_index("ix_social_posts_status", "social_posts", ["status"])
    op.create_index("ix_social_posts_scheduled", "social_posts", ["scheduled_at"])
    op.create_index("ix_social_posts_campaign", "social_posts", ["campaign_id"])

    # --- social_post_targets --------------------------------------------------
    op.create_table(
        "social_post_targets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("post_id", sa.Uuid(), sa.ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", sa.Uuid(), sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("provider_post_id", sa.String(length=255), nullable=True),
        sa.Column("provider_post_url", sa.String(length=1000), nullable=True),
        sa.Column("provider_container_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analytics", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("analytics_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("post_id", "social_account_id", name="uq_post_target_account"),
    )
    op.create_index("ix_social_post_targets_post", "social_post_targets", ["post_id"])
    op.create_index("ix_social_post_targets_account", "social_post_targets", ["social_account_id"])
    op.create_index("ix_social_post_targets_status", "social_post_targets", ["status"])

    # --- social_audit_logs ----------------------------------------------------
    op.create_table(
        "social_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("post_id", sa.Uuid(), sa.ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("details", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_social_audit_post", "social_audit_logs", ["post_id"])
    op.create_index("ix_social_audit_created", "social_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("social_audit_logs")
    op.drop_table("social_post_targets")
    op.drop_table("social_posts")
    op.drop_table("social_accounts")
