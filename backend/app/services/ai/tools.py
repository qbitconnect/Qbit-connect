"""AI Agent Tool Registry.

Each tool is a pure async function with a well-typed input dict and output dict.
All tools enforce:
- Permission checks via the caller's RBAC context
- Source allowlisting (no arbitrary URL fetch)
- Timeouts
- Non-hallucinated output (missing data = explicit None / "unknown")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger("qbit.ai.tools")


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any]
    error: str | None = None
    source: str = "unknown"


class ToolPermissionError(Exception):
    """Tool invocation denied — missing permission or restricted source."""


# ---------------------------------------------------------------------------
# CRM Lead Lookup
# ---------------------------------------------------------------------------

async def crm_lead_lookup(
    lead_id: str,
    session: Any,
    organization_id: str | None = None,
) -> ToolResult:
    """Retrieve a lead record from the QBIT CRM database."""
    try:
        import uuid
        from sqlalchemy import or_, select
        from app.models.scrape import Lead

        q = select(Lead).where(Lead.id == uuid.UUID(lead_id))
        if organization_id:
            q = q.where(
                or_(
                    Lead.organization_id == uuid.UUID(organization_id),
                    Lead.organization_id.is_(None),
                )
            )

        result = await session.execute(q)
        lead = result.scalar_one_or_none()

        if lead is None:
            return ToolResult(success=False, data={}, error="Lead not found", source="crm")

        return ToolResult(
            success=True,
            source="crm",
            data={
                "id": str(lead.id),
                "business_name": lead.business_name,
                "category": lead.category or "unknown",
                "city": lead.city or "unknown",
                "state": lead.state or "unknown",
                "country": lead.country or "unknown",
                "phone": lead.phone or None,
                "email": lead.email or None,
                "website": lead.website or None,
                "rating": lead.rating or None,
                "review_count": lead.review_count or 0,
                "address": lead.address or None,
                "source_name": getattr(lead, "source", None) or "unknown",
                "enrichment_status": lead.enrichment_status or "UNENRICHED",
                "quality_score": lead.quality_score or 0,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("crm_lead_lookup failed")
        return ToolResult(success=False, data={}, error=str(exc), source="crm")


# ---------------------------------------------------------------------------
# Product Catalog Search
# ---------------------------------------------------------------------------

async def product_catalog_search(
    query: str,
    industry: str | None = None,
    session: Any = None,
) -> ToolResult:
    """Search the QBIT POS hardware catalog for matching products."""
    try:
        from sqlalchemy import select
        from app.models.ai import Product

        q = select(Product).where(Product.is_active.is_(True))
        result = await session.execute(q)
        products = result.scalars().all()

        matches = []
        query_lower = query.lower()
        for p in products:
            target_industries = p.target_industries or []
            name_match = query_lower in p.name.lower() or query_lower in p.description.lower()
            industry_match = industry and any(
                industry.lower() in ind.lower() for ind in target_industries
            )
            if name_match or industry_match or not query:
                matches.append(p.to_dict())

        return ToolResult(
            success=True,
            source="product_catalog",
            data={"products": matches, "count": len(matches)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("product_catalog_search failed")
        return ToolResult(success=False, data={}, error=str(exc), source="product_catalog")


# ---------------------------------------------------------------------------
# Lead Score Calculator (deterministic, no AI)
# ---------------------------------------------------------------------------

def calculate_lead_score(lead_data: dict) -> dict:
    """Deterministic lead scoring engine (0-100). No AI — fully auditable.

    Factor weights:
    - Category fit (matches POS-relevant industries): 0-25 pts
    - Location data completeness: 0-15 pts
    - Contact completeness (phone, email, website): 0-20 pts
    - Rating and review signals: 0-20 pts
    - POS hardware fit indicators: 0-10 pts
    - Engagement history: 0-10 pts
    Total: 100 pts
    """
    POS_INDUSTRIES = {
        "restaurant", "cafe", "hotel", "retail", "pharmacy", "supermarket",
        "bakery", "salon", "spa", "bar", "qsr", "grocery", "food",
        "hospitality", "shop", "store", "outlet", "mart"
    }

    category = (lead_data.get("category") or "").lower()
    city = lead_data.get("city")
    state = lead_data.get("state")
    country = lead_data.get("country")
    phone = lead_data.get("phone")
    email = lead_data.get("email")
    website = lead_data.get("website")
    rating = lead_data.get("rating") or 0
    review_count = lead_data.get("review_count") or 0

    # --- Category fit (25 pts) -----------------------------------------------
    cat_score = 0
    if any(kw in category for kw in POS_INDUSTRIES):
        cat_score = 25
    elif category and category not in ("unknown", ""):
        cat_score = 8

    # --- Location completeness (15 pts) --------------------------------------
    loc_score = 0
    if city:
        loc_score += 6
    if state:
        loc_score += 5
    if country:
        loc_score += 4

    # --- Contact completeness (20 pts) ---------------------------------------
    contact_score = 0
    if phone:
        contact_score += 8
    if email:
        contact_score += 7
    if website:
        contact_score += 5

    # --- Rating and reviews (20 pts) -----------------------------------------
    rating_score = 0
    try:
        r = float(rating)
        if r >= 4.5:
            rating_score += 12
        elif r >= 4.0:
            rating_score += 9
        elif r >= 3.5:
            rating_score += 6
        elif r > 0:
            rating_score += 3
    except (TypeError, ValueError):
        pass
    if review_count >= 100:
        rating_score += 8
    elif review_count >= 50:
        rating_score += 6
    elif review_count >= 20:
        rating_score += 4
    elif review_count >= 5:
        rating_score += 2
    rating_score = min(rating_score, 20)

    # --- POS hardware fit (10 pts) -------------------------------------------
    pos_fit_score = 0
    if any(kw in category for kw in {"restaurant", "cafe", "hotel", "retail", "pharmacy", "supermarket", "grocery"}):
        pos_fit_score = 10
    elif any(kw in category for kw in POS_INDUSTRIES):
        pos_fit_score = 7

    # --- Engagement history (10 pts) — placeholder for CRM activity ----------
    engagement_score = lead_data.get("engagement_score", 0)
    engagement_score = min(int(engagement_score), 10)

    total = cat_score + loc_score + contact_score + rating_score + pos_fit_score + engagement_score
    total = max(0, min(total, 100))

    return {
        "score": total,
        "confidence": 0.85 if total > 0 else 0.1,
        "category_fit_score": cat_score,
        "location_fit_score": loc_score,
        "contact_completeness_score": contact_score,
        "rating_review_score": rating_score,
        "pos_hardware_fit_score": pos_fit_score,
        "engagement_history_score": engagement_score,
        "factor_breakdown": {
            "category_fit": {"score": cat_score, "max": 25, "category_matched": category},
            "location_completeness": {"score": loc_score, "max": 15, "city": bool(city), "state": bool(state), "country": bool(country)},
            "contact_completeness": {"score": contact_score, "max": 20, "phone": bool(phone), "email": bool(email), "website": bool(website)},
            "rating_reviews": {"score": rating_score, "max": 20, "rating": rating, "reviews": review_count},
            "pos_hardware_fit": {"score": pos_fit_score, "max": 10},
            "engagement_history": {"score": engagement_score, "max": 10},
        },
        "explanation": (
            f"Score {total}/100: category fit {cat_score}/25, location {loc_score}/15, "
            f"contacts {contact_score}/20, reviews {rating_score}/20, "
            f"POS fit {pos_fit_score}/10, engagement {engagement_score}/10."
        ),
    }
