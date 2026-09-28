"""Phase 9 — AI Agents, Lead Intelligence & Sales Copilot.

NON-DESTRUCTIVE:
- creates ai_agent_definitions table for configurable agent registry
- creates ai_agent_runs table for durable execution records
- creates ai_agent_steps table for step-level tool execution
- creates lead_intelligence table for enriched business intelligence
- creates lead_score_records table for transparent scoring
- creates sales_briefs table for product-matched sales intelligence
- creates lead_enrichment_proposals table for human review workflow
- creates ai_usage_records table for token/cost accounting
- creates products table for QBIT POS hardware catalog
- seeds 7 initial QBIT POS products

Revision ID: 0017_ai_agents_intelligence
Revises: 0016_social_media_publishing
Create Date: 2026-09-27
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_ai_agents_intelligence"
down_revision = "0016_social_media_publishing"
branch_labels = None
depends_on = None

PortableJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # --- ai_agent_definitions --------------------------------------------------
    op.create_table(
        "ai_agent_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model_routing_key", sa.String(length=50), nullable=False, server_default="reasoning"),
        sa.Column("preferred_model", sa.String(length=100), nullable=True),
        sa.Column("allowed_tools", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("allowed_sources", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("execution_limits", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", "version", "organization_id", name="uq_agent_slug_version_org"),
    )
    op.create_index("ix_ai_agent_slug_org", "ai_agent_definitions", ["slug", "organization_id"])

    # --- ai_agent_runs ---------------------------------------------------------
    op.create_table(
        "ai_agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_definition_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_definitions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", sa.Uuid(),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=True),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="QUEUED"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("execution_plan", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("input_data", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("output_data", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("provider_used", sa.String(length=50), nullable=True),
        sa.Column("model_used", sa.String(length=100), nullable=True),
        sa.Column("token_usage", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("cost_estimate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_review_approved", sa.Boolean(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_agent_runs_org", "ai_agent_runs", ["organization_id"])
    op.create_index("ix_ai_agent_runs_status", "ai_agent_runs", ["status"])
    op.create_index("ix_ai_agent_runs_lead", "ai_agent_runs", ["lead_id"])
    op.create_index("ix_ai_agent_runs_idempotency", "ai_agent_runs", ["idempotency_key"])

    # --- ai_agent_steps --------------------------------------------------------
    op.create_table(
        "ai_agent_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_name", sa.String(length=200), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("tool_input", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("tool_output", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_agent_steps_run", "ai_agent_steps", ["run_id"])
    op.create_index("ix_ai_agent_steps_status", "ai_agent_steps", ["status"])

    # --- lead_intelligence -----------------------------------------------------
    op.create_table(
        "lead_intelligence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("lead_id", sa.Uuid(),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("business_summary", sa.Text(), nullable=True),
        sa.Column("business_category", sa.String(length=100), nullable=True),
        sa.Column("business_subcategory", sa.String(length=100), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("verified_location", sa.String(length=500), nullable=True),
        sa.Column("coordinates", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("key_decision_makers", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("sentiment_signals", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("social_profiles", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("data_completeness", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("provenance_records", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", name="uq_lead_intelligence_lead"),
    )
    op.create_index("ix_lead_intelligence_lead", "lead_intelligence", ["lead_id"])
    op.create_index("ix_lead_intelligence_org", "lead_intelligence", ["organization_id"])

    # --- lead_score_records ----------------------------------------------------
    op.create_table(
        "lead_score_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("lead_id", sa.Uuid(),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("category_fit_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("location_fit_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contact_completeness_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating_review_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pos_hardware_fit_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement_history_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("factor_breakdown", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("is_overridden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("override_score", sa.Integer(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_scores_lead", "lead_score_records", ["lead_id"])
    op.create_index("ix_lead_scores_org", "lead_score_records", ["organization_id"])
    op.create_index("ix_lead_scores_score", "lead_score_records", ["score"])

    # --- sales_briefs ----------------------------------------------------------
    op.create_table(
        "sales_briefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("lead_id", sa.Uuid(),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("business_overview", sa.Text(), nullable=False),
        sa.Column("recommended_products", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("sales_opportunities", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("suggested_discovery_questions", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("possible_objections", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("recommended_next_action", sa.Text(), nullable=False),
        sa.Column("data_gaps", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sales_briefs_lead", "sales_briefs", ["lead_id"])
    op.create_index("ix_sales_briefs_org", "sales_briefs", ["organization_id"])

    # --- lead_enrichment_proposals ---------------------------------------------
    op.create_table(
        "lead_enrichment_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("lead_id", sa.Uuid(),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(),
                  sa.ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposed_changes", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_enrichment_proposals_lead", "lead_enrichment_proposals", ["lead_id"])
    op.create_index("ix_enrichment_proposals_status", "lead_enrichment_proposals", ["status"])

    # --- ai_usage_records ------------------------------------------------------
    op.create_table(
        "ai_usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_usage_org_created", "ai_usage_records", ["organization_id", "created_at"])
    op.create_index("ix_ai_usage_provider", "ai_usage_records", ["provider"])

    # --- products --------------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_inr", sa.Float(), nullable=False),
        sa.Column("specifications", PortableJSON, nullable=False, server_default="{}"),
        sa.Column("target_industries", PortableJSON, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
    )
    op.create_index("ix_products_sku", "products", ["sku"])
    op.create_index("ix_products_category", "products", ["category"])

    # --- Seed QBIT POS Product Catalog -----------------------------------------
    now = datetime.now(timezone.utc)
    products_table = sa.table(
        "products",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String),
        sa.column("sku", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("price_inr", sa.Float),
        sa.column("specifications", sa.JSON),
        sa.column("target_industries", sa.JSON),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(products_table, [
        {
            "id": uuid.uuid4(),
            "name": 'QBIT Windows POS Terminal 15"',
            "sku": "QPOS-WIN-15",
            "category": "WINDOWS_POS",
            "description": 'All-in-one 15" touch-screen Windows POS terminal with i5 processor, 8GB RAM, 256GB SSD. Runs full Windows 10/11; compatible with all major POS software. Ideal for restaurants, retail, and hospitality.',
            "price_inr": 45000.0,
            "specifications": {"screen": '15"', "os": "Windows 10/11", "cpu": "Intel i5", "ram": "8GB", "storage": "256GB SSD", "ports": "USB×4, HDMI, RJ-45, RS-232"},
            "target_industries": ["restaurant", "retail", "hotel", "cafe", "bakery", "supermarket", "pharmacy", "salon"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": 'QBIT Android POS Terminal 10"',
            "sku": "QPOS-AND-10",
            "category": "ANDROID_POS",
            "description": 'Compact 10" Android 12 POS terminal with built-in 58mm receipt printer, barcode scanner, and NFC reader. Cloud-ready with 4G connectivity. Perfect for QSR, food delivery, and mobile merchants.',
            "price_inr": 28000.0,
            "specifications": {"screen": '10"', "os": "Android 12", "ram": "4GB", "storage": "64GB", "connectivity": "WiFi + 4G", "printer": "58mm built-in", "nfc": True},
            "target_industries": ["qsr", "food delivery", "retail", "grocery", "pharmacy", "salon", "spa"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": 'QBIT Handy POS 6"',
            "sku": "QPOS-HAND-6",
            "category": "HANDY_POS",
            "description": "Handheld 6\" Android POS for table-side ordering and mobile payments. Rugged build, 10-hour battery, IP54 splash resistant. Wireless with WiFi and Bluetooth.",
            "price_inr": 18500.0,
            "specifications": {"screen": '6"', "os": "Android 11", "battery": "5000mAh", "ip_rating": "IP54", "connectivity": "WiFi + BT", "weight": "350g"},
            "target_industries": ["restaurant", "hotel", "cafe", "bar", "event management", "food court"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": "QBIT 80mm Thermal Receipt Printer",
            "sku": "QPT-80",
            "category": "THERMAL_PRINTER",
            "description": "High-speed 80mm thermal receipt printer with USB, Serial and Ethernet connectivity. 250mm/sec print speed, auto-cutter, supports ESC/POS commands. Compatible with all QBIT terminals and major POS software.",
            "price_inr": 6500.0,
            "specifications": {"width": "80mm", "speed": "250mm/sec", "interface": "USB + Serial + Ethernet", "autocutter": True, "protocol": "ESC/POS"},
            "target_industries": ["restaurant", "retail", "hotel", "cafe", "pharmacy", "supermarket"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": "QBIT 2D Barcode Scanner",
            "sku": "QBS-2D",
            "category": "BARCODE_SCANNER",
            "description": "Omnidirectional 2D barcode scanner supporting QR codes, DataMatrix, PDF417, and all 1D codes. USB HID plug-and-play. 1.5m drop-resistant housing. Ideal for retail inventory and pharmacy dispensing.",
            "price_inr": 3200.0,
            "specifications": {"type": "2D omnidirectional", "interface": "USB HID", "codes": ["QR", "DataMatrix", "PDF417", "EAN", "Code128"], "drop_resistance": "1.5m"},
            "target_industries": ["retail", "pharmacy", "supermarket", "warehouse", "hospital"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": "QBIT 4-Bill Cash Drawer",
            "sku": "QCD-4B",
            "category": "CASH_DRAWER",
            "description": "Heavy-duty steel cash drawer with 4 bill slots, 8 coin slots, RJ-11 kick-port and USB trigger. Anti-scratch powder-coat finish. Fits under most QBIT POS terminals.",
            "price_inr": 4800.0,
            "specifications": {"bill_slots": 4, "coin_slots": 8, "interface": "RJ-11 + USB", "material": "Heavy-duty steel", "dimensions": "410×415×100mm"},
            "target_industries": ["restaurant", "retail", "hotel", "cafe", "pharmacy", "supermarket", "bakery"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4(),
            "name": "QBIT POS Accessories Bundle",
            "sku": "QACC-BUNDLE",
            "category": "ACCESSORIES",
            "description": "Essential accessories bundle: customer display pole, stylus pen ×2, privacy screen filter, cable management kit, and spare receipt paper rolls (10 pack). Compatible with all QBIT POS terminals.",
            "price_inr": 2200.0,
            "specifications": {"includes": ["Customer pole display", "Stylus ×2", "Privacy screen filter", "Cable kit", "Paper rolls ×10"]},
            "target_industries": ["restaurant", "retail", "hotel", "cafe", "pharmacy", "salon"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    ])


def downgrade() -> None:
    op.drop_table("lead_enrichment_proposals")
    op.drop_table("ai_usage_records")
    op.drop_table("sales_briefs")
    op.drop_table("lead_score_records")
    op.drop_table("lead_intelligence")
    op.drop_table("ai_agent_steps")
    op.drop_table("ai_agent_runs")
    op.drop_table("ai_agent_definitions")
    op.drop_table("products")
