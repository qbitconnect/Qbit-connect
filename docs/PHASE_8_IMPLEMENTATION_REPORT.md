# Phase 8 Implementation Report: Social Media Integration, Content Publishing, Scheduling & Analytics

**Company:** QbitPro India Pvt Ltd.  
**System:** Qbit Connect (B2B Lead Generation, CRM & Marketing Automation SaaS)  
**Date:** September 2026  
**Status:** Complete & Verified  

---

## 1. Architecture Reused from Previous Phases

Phase 8 was constructed directly on the unified multi-tenant architecture established in Phases 1 through 7:
- **Authentication & RBAC (Phase 1 & Phase 11):** Reused `UserSession`, JWT authorization, password hashing, and role hierarchies. Added 7 canonical `social.*` permissions to the core permission catalog (`social.view`, `social.create`, `social.schedule`, `social.publish`, `social.approve`, `social.manage_accounts`, `social.analytics`) mapped across `SUPER_ADMIN`, `ADMIN`, `MARKETING_MANAGER`, and `MARKETING_EXECUTIVE`.
- **Database Layer & Multi-Tenancy (Phase 1, 5 & 6):** Reused SQLAlchemy 2.0 async engine, PostgreSQL/SQLite compatibility layer (`PortableJSON`, UUID primary keys, UTC timestamp helpers), and tenant isolation patterns.
- **Provider Registry Architecture (Phase 6 & Phase 7):** Extended `MarketingProviderRegistry` to register official social provider adapters (`MetaFacebookProvider`, `InstagramGraphProvider`, `LinkedInProvider`) and the deterministic test mock (`SocialMockProvider`).
- **CRM Campaign Linking & Timeline Events (Phase 5 & 6):** Connected social posts to CRM marketing campaigns (`Campaign`) and recorded lifecycle audit events (`CampaignEvent`) upon dispatch completion.
- **Operator UI & Stitch Design System (Phase 1–7):** Preserved 100% of the Google Stitch visual identity, fonts, tokenized color palettes, navigation layout (`base.html`), and modal conventions.

---

## 2. Files Created and Modified

### Created Files
- `backend/app/models/social.py`: Relational models `SocialAccount`, `SocialPost`, `SocialPostTarget`, and `SocialAuditLog` with explicit enums and display-safe public dictionary projections.
- `backend/alembic/versions/0016_social_media_publishing.py`: Non-destructive database migration adding `social_accounts`, `social_posts`, `social_post_targets`, and `social_audit_logs` tables with indices and foreign keys.
- `backend/app/services/marketing/providers/social/base.py`: Abstract base class `BaseSocialProvider` with canonical camelCase and snake_case method bindings via `__init_subclass__`.
- `backend/app/services/marketing/providers/social/facebook.py`: Production Meta Facebook Pages adapter for Meta Graph API v20.0.
- `backend/app/services/marketing/providers/social/instagram.py`: Production Instagram Business/Creator adapter implementing container-then-publish lifecycle.
- `backend/app/services/marketing/providers/social/linkedin.py`: Production LinkedIn adapter for REST Posts and Share Statistics APIs.
- `backend/app/services/marketing/providers/social/mock.py`: Deterministic, zero-network test mock provider.
- `backend/app/services/social/media.py`: SSRF IP guard, MIME and size validators, and HMAC SHA-256 signed temporary media token generator/verifier.
- `backend/app/services/social/service.py`: `SocialPostService` domain logic for CRUD, drafts, duplication, platform validation, and approval transitions.
- `backend/app/services/social/publisher.py`: `SocialPublisher` and `SocialPublishingWorker` background queue consumer with idempotency keys and retry caps.
- `backend/app/services/social/analytics.py`: `SocialAnalyticsService` for provider insights synchronization and calendar queries.
- `backend/app/api/v1/social.py`: REST API router mounted at `/api/v1/social`.
- `backend/app/ui/social.py`: Operator UI router mounted at `/social`.
- `backend/app/templates/social/index.html`: Social Media Hub (Overview, Connected Accounts, Publishing Queue).
- `backend/app/templates/social/composer.html`: Multi-platform content composer with real-time character counters and live previews.
- `backend/app/templates/social/calendar.html`: Visual content calendar grid grouped by scheduled publish dates.
- `backend/app/templates/social/approval.html`: Team lead post review, approval, and rejection queue.
- `backend/app/templates/social/analytics.html`: Real provider performance analytics dashboard.
- `backend/tests/test_phase8_social.py`: Automated test suite covering 10 functional criteria.

### Modified Files
- `backend/app/models/__init__.py`: Exported social models.
- `backend/app/services/marketing/providers/__init__.py`: Registered Facebook, Instagram, LinkedIn, and Mock social providers in `build_provider_registry`.
- `backend/app/services/rbac.py`: Seeded 7 `social.*` permissions and mapped them to platform roles.
- `backend/app/main.py`: Mounted `/api/v1/social` API router and `/social` UI router.
- `backend/app/templates/base.html`: Activated Social Media navigation link pointing to `/social`.
- `backend/.env.example`: Added configuration variables for Meta and LinkedIn OAuth and media safety.

---

## 3. Database Changes and Migration Instructions

### Tables Added
1. `social_accounts`:
   - `id` (UUID PK), `organization_id`, `created_by`
   - `platform` (`FACEBOOK`, `INSTAGRAM`, `LINKEDIN`), `account_type` (`PAGE`, `BUSINESS`, `ORGANIZATION`, `PERSONAL`)
   - `account_id` (External platform ID, unique per platform + org), `account_name`, `username`, `profile_picture_url`
   - `status` (`ACTIVE`, `EXPIRED`, `REVOKED`, `DISCONNECTED`), `status_message`
   - `encrypted_credentials` (Text, protected access token), `token_expires_at`
   - `scopes` (JSON array of granted scopes), `metadata_json` (JSON details)
   - `created_at`, `updated_at`, `last_synced_at`
2. `social_posts`:
   - `id` (UUID PK), `organization_id`, `author_id`, `campaign_id` (FK to CRM campaigns)
   - `title`, `caption`, `platform_customizations` (JSON per-platform text overrides), `media_urls` (JSON array)
   - `status` (`DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `SCHEDULED`, `PUBLISHING`, `PUBLISHED`, `FAILED`, `CANCELLED`)
   - `approval_status` (`DRAFT`, `PENDING`, `APPROVED`, `REJECTED`), `approver_id`, `rejection_reason`
   - `scheduled_at`, `timezone`, `published_at`, `created_at`, `updated_at`
3. `social_post_targets`:
   - `id` (UUID PK), `post_id` (FK cascade), `social_account_id` (FK cascade)
   - `platform`, `status` (`PENDING`, `QUEUED`, `PUBLISHING`, `PUBLISHED`, `FAILED`, `CANCELLED`)
   - `provider_post_id`, `provider_post_url`, `provider_container_id`
   - `error_message`, `error_code`, `retry_count`, `max_retries`, `idempotency_key`
   - `published_at`, `analytics` (JSON metrics), `analytics_updated_at`
4. `social_audit_logs`:
   - `id` (UUID PK), `post_id` (FK cascade), `actor_id`, `event_type`, `details` (JSON), `created_at`

### Migration Command
```bash
cd backend
alembic upgrade head
```

---

## 4. Platforms and Account Types Supported

1. **Meta Facebook:**
   - **Supported Account Types:** Facebook Pages (managed pages with Page Access Tokens). Personal Facebook user profiles are explicitly unsupported by Meta Graph API v20.0 for automated publishing.
   - **Supported Formats:** Feed text updates, single image uploads (`/photos`), single video uploads (`/videos`), multi-photo carousels.
2. **Instagram:**
   - **Supported Account Types:** Instagram Business accounts and Instagram Creator accounts connected to a Facebook Page. Personal Instagram accounts are unsupported by Meta's Graph API.
   - **Supported Formats:** Single photos (`IMAGE`), single reels/videos (`VIDEO`), and multi-item carousels (`CAROUSEL`). Text-only posts are rejected at validation per Instagram Graph API rules.
3. **LinkedIn:**
   - **Supported Account Types:** LinkedIn Organization Pages (Company Pages) and Authenticated Member Profiles (`urn:li:person` / `urn:li:organization`).
   - **Supported Formats:** Organic text posts, single image posts (via Assets API), multi-media posts, and external URL link shares.

---

## 5. OAuth Configuration and Permissions

### Scopes Required
- **Meta Facebook Pages:**
  - `pages_show_list`: Discover managed Facebook Pages.
  - `pages_read_engagement`: Read engagement metrics for posts.
  - `pages_manage_posts`: Publish posts and photos to Pages.
  - `pages_read_user_content`: Read posts and comments.
  - `business_management`: Read connected Instagram accounts.
- **Instagram Graph API:**
  - `instagram_basic`: Read basic profile information.
  - `instagram_content_publish`: Create containers and publish media.
  - `instagram_manage_insights`: Retrieve impressions, reach, engagement, and likes.
- **LinkedIn:**
  - `openid`, `profile`, `email`: Authenticate the member.
  - `w_member_social`: Publish text and media to personal profile.
  - `w_organization_social`: Publish to organization company pages.
  - `r_organization_social`: Read page insights and post statistics.

### Security Mechanisms
- **State Parameter:** CSRF mitigation via HMAC-signed, time-limited state tokens binding the user ID and tenant.
- **Secret Redaction:** `encrypted_credentials` are stripped from all public API outputs via `SocialAccount.to_public_dict()`. Access tokens are never returned to the UI or logs.
- **HMAC Webhook Verification:** SHA-256 webhook signatures (`X-Hub-Signature-256`) verified in constant time.

---

## 6. Publishing Lifecycle and Durable Scheduler

```
Draft Created (SocialPostStatus.DRAFT)
       │
       ▼
Submit for Approval ──► (PENDING_APPROVAL)
       │                         │
       │ Rejected                │ Approved
       ▼                         ▼
(REJECTED)                  (APPROVED)
                                 │
                   ┌─────────────┴─────────────┐
                   │ Immediate                 │ Scheduled
                   ▼                           ▼
            (PUBLISHING)               (SCHEDULED)
                   │                           │ Worker polls
                   ▼                           ▼
            Target Dispatch ────────────► (PUBLISHING)
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
    (PUBLISHED)          (FAILED) ──► Backoff Retry (Max 3)
```

- **Idempotency:** Every `SocialPostTarget` generates a unique `idempotency_key` preventing duplicate post dispatches on network retries.
- **Instagram Two-Step Lifecycle:**
  1. POST container creation (`/{ig-user-id}/media`)
  2. Poll status until container is `FINISHED`
  3. POST media publish (`/{ig-user-id}/media_publish`)
- **Durable Worker:** `SocialPublishingWorker` runs continuously with `asyncio.sleep(15)`, querying scheduled posts due for publication and executing target dispatches.

---

## 7. Media Storage and Security (Anti-SSRF Protection)

- **Anti-SSRF IP Guard:** `is_safe_remote_url(url)` resolves the DNS hostname and strictly verifies the target IP against non-routable CIDR blocks:
  - Private networks: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
  - Loopback: `127.0.0.0/8`, `::1/128`
  - Link-Local & Cloud Metadata: `169.254.0.0/16`, `fe80::/10`
  - Multicast & Carrier-Grade NAT: `224.0.0.0/4`, `100.64.0.0/10`
- **MIME & File Size Validation:**
  - Allowed image types: `image/jpeg`, `image/png`, `image/webp`, `image/gif` (Max: 10 MB)
  - Allowed video types: `video/mp4`, `video/quicktime` (Max: 100 MB)
  - Inspects file magic bytes (e.g. `\xff\xd8\xff` for JPEG, `\x89PNG` for PNG) rather than trusting untrusted extensions.
- **Signed Temporary Media Tokens:** Generates HMAC SHA-256 signed access URLs with a 2-hour expiration window for social platform media download ingestion.

---

## 8. Content Calendar and Approval Workflow

- **Team Roles:**
  - `MARKETING_EXECUTIVE` / `OPERATOR`: Can create drafts, edit content, and submit for approval.
  - `MARKETING_MANAGER` / `ADMIN` / `SUPER_ADMIN`: Can approve, reject with feedback reasons, and schedule/publish posts.
- **Audit Log Trail:** Every status change (`CREATED`, `EDITED`, `SUBMITTED_FOR_APPROVAL`, `APPROVED`, `REJECTED`, `SCHEDULED`, `PUBLISHED`, `CANCELLED`) writes an immutable record to `social_audit_logs`.
- **Content Calendar:** `/social/calendar` displays scheduled and published items color-coded by platform (`Facebook`, `Instagram`, `LinkedIn`) with time slots and quick preview modals.

---

## 9. Analytics and Real Data Sources (Zero Hallucination)

- **Strict No-Hallucination Policy:** Metrics are only populated when retrieved from official provider APIs. Missing or unavailable platform metrics are stored as `None` or explicitly flagged as `available: False`.
- **Metrics Collected:**
  - **Facebook Pages:** `post_impressions`, `post_engagements`, `post_reactions_like_total`, `post_clicks`.
  - **Instagram Business:** `impressions`, `reach`, `engagement`, `saved`, `likes`, `comments`.
  - **LinkedIn:** `impressions`, `clicks`, `likes`, `comments`, `shares`, `engagement_rate`.
- **Sync Trigger:** Operators can trigger on-demand sync from `/social/analytics` or via `POST /api/v1/social/targets/{id}/sync-analytics`.

---

## 10. UI Routes and Components Connected

All UI routes preserve 100% of the Google Stitch styling:
- `GET /social`: Overview dashboard with connected accounts summary, active queue, and 30-day reach metrics.
- `GET /social/composer`: Multi-account composer with dynamic platform selector, character limit counters (Facebook: 63,206; Instagram: 2,200; LinkedIn: 3,000), hashtag validator, media attachments, and preview cards.
- `POST /social/composer`: Form handler supporting immediate publish, scheduling, team approval submission, and drafting.
- `GET /social/calendar`: Visual grid view grouped by scheduled dates.
- `GET /social/approval`: Pending review queue for managers with one-click approve and rejection reason modal.
- `GET /social/analytics`: Aggregated engagement dashboard with per-post performance tables.

---

## 11. Test Execution Results

All automated tests passed with zero failures:

### Phase 8 Dedicated Test Suite (`tests/test_phase8_social.py`)
```
tests/test_phase8_social.py::test_social_provider_canonical_methods PASSED
tests/test_phase8_social.py::test_account_connection_and_discovery PASSED
tests/test_phase8_social.py::test_post_creation_drafting_and_duplication PASSED
tests/test_phase8_social.py::test_platform_limits_and_validation PASSED
tests/test_phase8_social.py::test_approval_workflow_lifecycle PASSED
tests/test_phase8_social.py::test_scheduling_and_cancellation PASSED
tests/test_phase8_social.py::test_publishing_engine_dispatch_and_idempotency PASSED
tests/test_phase8_social.py::test_ssrf_protection_and_signed_media_tokens PASSED
tests/test_phase8_social.py::test_social_analytics_synchronization PASSED
tests/test_phase8_social.py::test_social_ui_views_render_cleanly PASSED

======================== 10 passed in 23.69s =========================
```

### Cross-Phase Regression Test Suite (Phases 2 through 8)
```
tests/test_phase8_social.py ..........                                   [  7%]
tests/test_phase7_whatsapp.py ..........                                 [ 15%]
tests/test_phase6_email.py ...........                                   [ 24%]
tests/test_phase5_crm.py ...........                                     [ 33%]
tests/test_phase4_website_and_directory.py ...............               [ 45%]
tests/test_phase3_google_maps.py ................                        [ 57%]
tests/test_phase2_scraper_orchestration.py ..........                    [ 65%]
tests/inbox/test_api.py ..............                                   [ 76%]
tests/inbox/test_reply.py .........                                      [ 84%]
tests/inbox/test_scenarios.py ............                               [ 93%]
tests/inbox/test_webhooks_security.py ........                           [100%]

================= 126 passed in 238.99s (0:03:58) ==================
```

---

## 12. Build and Lint Results

- **Python Syntax & Type Checks:** All imports resolved cleanly. Verified with `pytest` under Python 3.12.10.
- **FastAPI Application Boot:** Application booted with 53 routes registered across API and UI routers.
- **Database Engine Disposals:** Isolated clean teardown verified in every fixture.

---

## 13. Environment Variables

Added to `.env.example`:

```bash
# ==============================================================================
# Phase 8: Social Media Marketing & Publishing Integrations
# ==============================================================================
# Meta Facebook & Instagram Graph API (v20.0)
META_APP_ID=
META_APP_SECRET=
META_API_VERSION=v20.0
META_WEBHOOK_VERIFY_TOKEN=

# LinkedIn Developer App (Community Management & Share APIs)
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=

# Public Base URL for OAuth Callbacks & Webhook Ingestion
QBIT_PUBLIC_BASE_URL=https://connect.qbitpro.com

# Media Safety & Storage
SOCIAL_MEDIA_TOKEN_SECRET=
SOCIAL_MEDIA_UPLOAD_DIR=./data/social_media
```

---

## 14. Incomplete Features and External Blockers

- **Meta App Review & Business Verification:** Live publishing to non-developer Facebook Pages or Instagram accounts requires submitting the Meta App for App Review with `pages_manage_posts`, `instagram_content_publish`, and completing Meta Business Verification.
- **LinkedIn Marketing Developer Program:** Publishing to LinkedIn Company Pages requires applying for and being granted access to the LinkedIn Community Management API product.

---

## 15. Live Publishing vs Mock Verification

- **Automated Verification:** The automated test suite executes deterministically using `SocialMockProvider` and local SQLite engines without making outbound network requests or consuming API quotas.
- **Production Provider Readiness:** `MetaFacebookProvider`, `InstagramGraphProvider`, and `LinkedInProvider` are fully implemented with real HTTP requests via `httpx` to Meta Graph API v20.0 and LinkedIn REST API endpoints, ready for live credentials once App Review is complete.

---

## 16. How to Run Migrations, Worker & Verification Tests

### 1. Apply Database Migration
```bash
cd backend
alembic upgrade head
```

### 2. Start the Backend Web Application
```bash
cd backend
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Run the Phase 8 Test Suite
```bash
cd backend
.\venv\Scripts\python.exe -m pytest tests/test_phase8_social.py -v
```

### 4. Run the Full Cross-Phase Regression Suite
```bash
cd backend
.\venv\Scripts\python.exe -m pytest tests/test_phase8_social.py tests/test_phase7_whatsapp.py tests/test_phase6_email.py tests/test_phase5_crm.py tests/test_phase4_website_and_directory.py tests/test_phase3_google_maps.py tests/test_phase2_scraper_orchestration.py tests/inbox/ -v
```
