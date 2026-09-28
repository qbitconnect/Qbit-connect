# QBIT GROWTH OS — IMPLEMENTATION ROADMAP
**Company:** QbitPro India Pvt Ltd.  
**Execution Standard:** Production-grade, incremental, verifiable implementation. No mock responses in production.

---

## Roadmap Phases & Milestones

```
Phase 1: Repository & Stitch Audit (COMPLETED)
   │
   ▼
Phase 2: Core Backend, Security & RBAC Alignment (IN PROGRESS)
   │  ├── Ensure CEO & 5-6 company roles mapped in RBAC
   │  ├── Mount Stitch Approved UI Templates into FastAPI Jinja2
   │  └── Verify Auth, Session Revocation, and Audit Trail
   │
   ▼
Phase 3: Scraper Engine & Actor Orchestration
   │  ├── Google Maps Scraper Actor Integration & Input Schema
   │  ├── Apify Actor Platform Adapter & Live Telemetry Stream
   │  └── Persistent Job Queue, Retry Policies & Cancellation
   │
   ▼
Phase 4: 24 Specialized Intelligence Agents (AG-01 to AG-24)
   │  ├── Google Maps (AG-01), Website Crawler (AG-03)
   │  ├── IndiaMART (AG-10), Justdial (AG-11), Hospitality (AG-12)
   │  └── Lead Enrichment & Deduplication (AG-23)
   │
   ▼
Phase 5: Lead CRM & Data Operations
   │  ├── High-density data grid & Lead Detail slide-out drawer
   │  ├── CSV/Excel mapping engine with batch validation
   │  └── Fuzzy deduplication & merge workflow
   │
   ▼
Phase 6: Email Marketing Pipeline
   │  ├── SMTP & API Connection verification
   │  ├── Template engine with safe variable interpolation
   │  └── Recipient delivery queue, tracking & unsubscribes
   │
   ▼
Phase 7: Authorized WhatsApp Business Platform
   │  ├── Meta Cloud API connection & WABA ID verification
   │  ├── Approved message template sync
   │  └── Webhook receiver with signature verification
   │
   ▼
Phase 8: Social Media & Automation Engine
   │  ├── Official Meta / LinkedIn connection adapters
   │  └── Node-based workflow builder execution runtime
   │
   ▼
Phase 9: CEO Command Center, Employee Portals & Governance
   │  ├── Live aggregate telemetry for Sagar / CEO
   │  ├── Role-based workspace views (Marketing vs Research)
   │  └── Immutable security audit ledger
   │
   ▼
Phase 10: Automated Testing & Visual Acceptance
   │  ├── Unit & API integration tests
   │  ├── Role boundary and security tests
   │  └── End-to-end user journey verification
   │
   ▼
Phase 11: Production Readiness & Hardening
      ├── Environment secrets validation
      ├── Health probes & monitoring
      └── Backup & recovery runbook
```

---

## Phase Dependencies & Exit Criteria

| Phase | Core Deliverables | Verification / Acceptance Gate |
|---|---|---|
| **Phase 1** | 6 Architecture & Audit Documents | Complete analysis of existing code and 50+ Stitch screens |
| **Phase 2** | Role alignment (`CEO`, `Admin`, `Marketing Manager`, `Marketing Executive`, `Researcher`), Stitch UI template mounting | Auth flow, protected routes, and session enforcement passing unit tests |
| **Phase 3** | Actor registry, execution state machine, live run terminal | Scraper run transitions from PENDING ➡️ RUNNING ➡️ COMPLETED with persistent logs |
| **Phase 4** | Scraper adapters (AG-01 to AG-24) with schema checks | Controlled extraction tests with real normalized output schemas |
| **Phase 5** | Lead table, drawer, import mapper, dedup merge | 100% data persistence, zero fake rows, provenance tracked |
| **Phase 6** | SMTP connection, email template editor, send queue | Real email delivery probe, DKIM/SPF check, unsubscribe handling |
| **Phase 7** | WhatsApp Cloud API connection, template selector | Valid WABA credentials test, incoming delivery webhook |
| **Phase 8** | Automation runtime & official social adapters | Workflow triggers fire reliably without infinite loops |
| **Phase 9** | CEO Command Center & Audit Center | Real-time aggregate queries and immutable audit entries |
| **Phase 10** | Automated test suite expansion | All automated unit, integration, and security tests green |
| **Phase 11** | Production config, deployment scripts, monitoring | System passes release readiness checklist |
