# Phase 9 Implementation Report: AI Agents, Lead Intelligence & Sales Copilot

**Company:** QbitPro India Pvt Ltd.  
**System:** Qbit Connect (B2B Lead Generation, CRM & Marketing Automation SaaS)  
**Date:** September 2026  
**Status:** Complete & Verified  

---

## 1. Architecture Reused from Previous Phases

Phase 9 was constructed directly on the unified multi-tenant architecture established in Phases 1 through 8:
- **Authentication & RBAC (Phase 1, Phase 8 & Phase 11):** Reused `UserSession`, JWT authorization, password hashing, and role hierarchies. Added 9 canonical Phase 9 permissions to the core catalog (`ai.view`, `ai.run_agents`, `ai.manage_agents`, `ai.view_usage`, `leads.enrich`, `leads.view_intelligence`, `leads.view_score`, `products.view`, `products.manage`), mapped across `ROLE_SUPER_ADMIN`, `ROLE_CEO`, `ROLE_ADMIN`, and `ROLE_MANAGER`.
- **Database Layer & Multi-Tenancy (Phases 1–8):** Reused SQLAlchemy 2.0 async engine, PostgreSQL/SQLite compatibility layer (`PortableJSON`, UUID primary keys, UTC timestamp helpers, `selectinload`), and tenant isolation patterns.
- **Worker & Durable Queue Foundation (Phase 2 & Phase 3):** Reused asynchronous worker orchestration patterns via `AIOrchestrationWorker` which polls `AIAgentRun` records in `QUEUED` status, executes steps durably, updates token/cost records, and records individual tool steps (`AIAgentStep`).
- **CRM Lead Pipeline & Workspace (Phase 4 & Phase 5):** Integrated directly with the canonical `Lead` model, updating enrichment status, business classifications, provenance citations, and creating `LeadEnrichmentProposal` records for human review before updating CRM fields.
- **Operator UI & Stitch Design System (Phases 1–8):** Followed Google Stitch design tokens, colors, typography, navigation layout (`base.html`), and modal/drawer conventions (`psychology` material symbol, 2-column Co-Pilot stream and tools sidebar).

---

## 2. Files Created and Modified

### Created Files
- `backend/app/models/ai.py`: Relational models:
  - `AIAgentDefinition`: Configurable agent registry with model routing and allowed tools.
  - `AIAgentRun`: Durable agent execution records with token/cost tracking and idempotency controls.
  - `AIAgentStep`: Step-level tool inputs/outputs, timing, and error logs.
  - `LeadIntelligence`: Rich business intelligence with field-level provenance and decision makers.
  - `LeadScoreRecord`: Deterministic 0–100 score with 6 factor weights and manual override capabilities.
  - `SalesBrief`: Sales intelligence connecting prospect needs to the QBIT POS hardware catalog.
  - `LeadEnrichmentProposal`: Proposed CRM field updates requiring human review before application.
  - `AIUsageRecord`: Provider token consumption and cost monitoring.
  - `Product`: QBIT POS hardware and accessories catalog.
- `backend/alembic/versions/0017_ai_agents_intelligence.py`: Non-destructive migration creating all 9 tables, indexes, constraints, and seeding 7 initial QBIT POS hardware products.
- `backend/app/services/ai/__init__.py`: Package marker.
- `backend/app/services/ai/gateway.py`: `AIGateway` HTTP provider abstraction (OpenAI-compatible) with model routing, token counting, cost estimation, SSRF private IP validation, mock mode for testing, and `AIUsageRecord` persistence.
- `backend/app/services/ai/tools.py`: Tool registry:
  - `crm_lead_lookup`: Secure database lookup of lead data.
  - `product_catalog_search`: Catalog search by keywords and industry fit.
  - `calculate_lead_score`: Deterministic, auditable 0–100 scoring engine.
- `backend/app/services/ai/agents.py`: Specialized agent implementations:
  - `BaseAgent`: Abstract durable step execution recorder.
  - `LeadEnrichmentAgent`: Enriches CRM leads with structured intelligence and generates enrichment proposals.
  - `LeadScoringAgent`: Runs deterministic scoring and records factor breakdowns.
  - `SalesIntelligenceAgent`: Generates sales briefs with catalog recommendations and discovery questions.
- `backend/app/services/ai/orchestrator.py`: `AIOrchestrationWorker` for durable queue execution.
- `backend/app/api/v1/ai.py`: Complete REST API endpoints mounted at `/api/v1/ai`.
- `backend/app/ui/ai.py`: Operator UI router mounted at `/ai-assistant`, `/ai/agents`, `/ai/runs/{id}`, `/ai/usage`, and `/leads/{id}/intelligence`.
- `backend/app/templates/ai/assistant.html`: AI Co-Pilot chat workspace with mode toggles, preflight checklist, and real-time execution display.
- `backend/app/templates/ai/agents.html`: AI Agent Registry with descriptions and scoring factor reference.
- `backend/app/templates/ai/run_detail.html`: Step-by-step execution timeline with duration, tokens, and errors.
- `backend/app/templates/ai/usage.html`: Token consumption and cost accounting dashboard by provider and model.
- `backend/app/templates/ai/lead_intelligence.html`: Lead drawer with score breakdown, business intelligence, decision makers, sentiment signals, sales brief, and enrichment proposals.
- `backend/tests/test_phase9_ai.py`: Comprehensive automated test suite covering all Phase 9 requirements.

### Modified Files
- `backend/app/models/__init__.py`: Exported all Phase 9 models to `Base.metadata`.
- `backend/app/core/config.py`: Added `AI_PROVIDER`, `AI_API_KEY`, `AI_BASE_URL`, `AI_DEFAULT_MODEL`, `AI_REASONING_MODEL`, `AI_MAX_TOKENS`, `AI_REQUEST_TIMEOUT`, `AI_MAX_COST_PER_RUN`, and runtime validation.
- `backend/app/services/rbac.py`: Seeded 9 `ai.*`, `leads.enrich`, `leads.view_intelligence`, `leads.view_score`, and `products.*` permissions.
- `backend/app/main.py`: Mounted `/api/v1/ai` API router and `/ai-assistant` UI routes.

---

## 3. Deterministic Lead Scoring Engine (0–100)

The scoring engine is implemented in `app/services/ai/tools.py` (`calculate_lead_score`) as a deterministic, fully auditable rule-based system with zero hallucination risk:

| Factor | Weight | Scoring Criteria |
|---|---|---|
| **Category Fit** | 0–25 pts | POS-relevant industries (Restaurants, Retail, Cafes, Hospitality, Supermarkets, Pharmacies, Salons) |
| **Contact Completeness** | 0–20 pts | Phone (+8 pts), Email (+7 pts), Website (+5 pts) |
| **Rating & Reviews** | 0–20 pts | Rating $\ge 4.5$ (+12 pts), $\ge 4.0$ (+9 pts), $\ge 3.5$ (+6 pts); Reviews $\ge 100$ (+8 pts), $\ge 50$ (+6 pts), $\ge 20$ (+4 pts) |
| **Location Completeness** | 0–15 pts | City (+6 pts), State (+5 pts), Country (+4 pts) |
| **POS Hardware Fit** | 0–10 pts | High-fit multi-terminal categories (+10 pts) |
| **Engagement History** | 0–10 pts | Verified customer interactions / CRM engagement score |
| **Total** | **0–100 pts** | Clamped strictly between 0 and 100 with comprehensive factor breakdown and human override capability |

---

## 4. QBIT POS Hardware Product Catalog

Seeded into the database in `0017_ai_agents_intelligence.py` with real Indian market pricing, specifications, and target industries:

1. **QBIT Windows POS Terminal 15" (`QPOS-WIN-15`):** ₹45,000 — All-in-one touch screen, i5, 8GB RAM, 256GB SSD, Windows 10/11. (Restaurants, Retail, Hotels, Supermarkets).
2. **QBIT Android POS Terminal 10" (`QPOS-AND-10`):** ₹28,000 — 10" Android 12, built-in 58mm thermal printer, scanner, 4G. (QSR, Food Delivery, Grocery).
3. **QBIT Handy POS 6" (`QPOS-HAND-6`):** ₹18,500 — Handheld 6" Android 11, IP54 splash-resistant, 10h battery. (Table-side ordering, Bars, Cafes).
4. **QBIT 80mm Thermal Receipt Printer (`QPT-80`):** ₹6,500 — 250mm/sec, auto-cutter, USB + Serial + Ethernet. (Restaurants, Retail, Pharmacies).
5. **QBIT 2D Barcode Scanner (`QBS-2D`):** ₹3,200 — Omnidirectional QR, DataMatrix, 1D/2D, 1.5m drop resistance. (Retail, Supermarkets).
6. **QBIT 4-Bill Cash Drawer (`QCD-4B`):** ₹4,800 — Heavy-duty steel, RJ-11 kick port, 4 bill / 8 coin slots. (Retail, Supermarkets, Restaurants).
7. **QBIT POS Accessories Bundle (`QACC-BUNDLE`):** ₹2,200 — Pole display, styluses, privacy filter, paper rolls. (All POS terminals).

---

## 5. Security & Defensive Design

1. **Zero Hallucination Guarantee:** When data is absent from scraper or CRM sources, agents explicitly output `null` or `"unknown"`. No phantom business data is ever fabricated.
2. **SSRF Guard:** The `AIGateway` validates `AI_BASE_URL` in production/staging environments, rejecting unencrypted HTTP schemes and private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, etc.).
3. **Prompt Injection Isolation:** Prompts strictly pin system instructions and role boundaries. User-provided lead data is encapsulated as JSON string data.
4. **Credential Protection:** Provider API keys (`AI_API_KEY`) remain strictly server-side, are never returned by any endpoint, and are excluded from all logging.
5. **Cost Budgeting:** Per-run cost cap (`AI_MAX_COST_PER_RUN = $0.50`) and granular token estimation per model prevents runaway expenditure.
6. **Human-in-the-Loop Review Gate:** AI enrichment proposals remain in `PENDING` state until an authorized manager explicitly approves them, at which point verified changes are safely merged into the CRM `Lead` record.

---

## 6. Test Suite & Verification Results

The automated test suite in `tests/test_phase9_ai.py` covers 12 functional criteria:
- `test_agent_registry_and_definitions`: Verified agent slugs and inheritance.
- `test_ai_gateway_mock_completion`: Verified mock completion, tokens, and model routing.
- `test_ai_gateway_ssrf_protection`: Verified rejection of HTTP and private IP targets.
- `test_deterministic_lead_scoring`: Verified multi-factor weights, bounds (0–100), and factor breakdown.
- `test_product_catalog_search`: Verified catalog retrieval by industry and keywords.
- `test_lead_enrichment_agent`: Verified intelligence creation and completeness score.
- `test_lead_scoring_agent`: Verified durable execution and `LeadScoreRecord` persistence.
- `test_sales_intelligence_agent`: Verified sales brief and product catalog linking.
- `test_ai_rest_api_lifecycle`: Verified 10 API endpoints (agents, runs, scoring, overrides, sales briefs, usage, products).
- `test_enrichment_proposal_review_workflow`: Verified approve/reject flow and CRM `Lead` mutation.
- `test_ai_rbac_permission_enforcement`: Verified 403 enforcement for unauthorized users.
- `test_ai_ui_views_render_cleanly`: Verified clean rendering of all 5 UI pages (`/ai-assistant`, `/ai/agents`, `/ai/runs/{id}`, `/ai/usage`, `/leads/{id}/intelligence`).

**Phase 9 Test Execution:**
```text
tests/test_phase9_ai.py ............ [100%]
12 passed in 28.76s
```
