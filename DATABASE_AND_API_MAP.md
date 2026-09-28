# QBIT GROWTH OS — DATABASE AND API SPECIFICATION
**Company:** QbitPro India Pvt Ltd.  
**Platform:** QBIT Growth OS (v2.4.0)

---

## 1. Multi-Tenancy & Workspace Data Hierarchy

```
Organization (QbitPro India Pvt Ltd)
  ├── Settings & Audit Records (Immutable)
  ├── Teams (e.g. Executive, Sales, Research)
  │     └── Team Members (Users mapped to roles)
  ├── Scraper Actors & Scrape Jobs
  │     ├── Job Logs (Streaming terminal lines)
  │     └── Datasets (Normalized JSON records)
  ├── Leads & Business Accounts
  │     ├── Contact Information (Phone, Email, Address, Website)
  │     ├── Enrichment Records (FSSAI, GST, Reviews, Social links)
  │     ├── Activity Timeline & Notes
  │     └── Tags & Saved Filter Views
  ├── Marketing Connections (Encrypted Credentials)
  │     ├── WhatsApp Business Accounts & Templates
  │     └── SMTP / Email Sending Accounts
  ├── Outbound Campaigns
  │     ├── Templates (Personalization tokens)
  │     └── Recipients (Delivery state machine, webhooks)
  └── Automation Workflows
        ├── Triggers (Lead created, Scrape completed, Webhook)
        ├── Conditions (Filter operators)
        └── Actions (Assign lead, Send notification, Sync CRM)
```

---

## 2. Core Database Entities

### 2.1 Identity, RBAC & Enterprise
* **`organizations`**: Multi-tenant container (`id`, `name`, `slug`, `status`, `created_at`).
* **`users`**: Platform operators (`id`, `email`, `password_hash`, `full_name`, `status`, `locked_until`, `failed_login_attempts`, `tokens_revoked_before`).
* **`roles` & `permissions`**: Granular role-to-permission mapping (`code`, `name`, `description`).
* **`teams` & `team_members`**: Departmental grouping with assigned `visibility_scope`.
* **`invitations`**: One-time hashed tokens for secure employee onboarding.
* **`user_sessions`**: Server-side active sessions keyed by JWT `jti`.
* **`audit_events`**: Tamper-evident ledger of all mutating administrative and data actions.

### 2.2 Scraper & Intelligence Entities
* **`scrapers`**: Built-in actor definitions (`slug`, `name`, `version`, `input_schema`, `is_active`).
* **`scrape_jobs`**: Execution instances (`id`, `scraper_id`, `status`, `started_at`, `finished_at`, `record_count`, `error_message`, `config`).
* **`scrape_job_logs`**: Timestamped execution messages (`job_id`, `level`, `message`, `created_at`).
* **`datasets` & `dataset_items`**: Extracted records saved with data provenance and source URLs.

### 2.3 CRM & Lead Operations
* **`leads`**: Centralized intelligence repository (`id`, `organization_id`, `company_name`, `domain`, `phone`, `email`, `city`, `state`, `country`, `rating`, `review_count`, `status`, `score`, `owner_id`).
* **`lead_tags` & `lead_tag_mappings`**: Audience segmentation tags.
* **`lead_notes` & `lead_activity`**: History trail and manual operator notes.
* **`import_batches`**: Audit records of CSV/XLSX lead imports.

### 2.4 Marketing & Communication
* **`connections`**: Encrypted API credentials for WhatsApp, Meta, LinkedIn, and SMTP.
* **`sending_accounts`**: Verified outbound channels with hourly rate limits.
* **`campaigns`**: Outbound sequences (`id`, `name`, `channel`, `status`, `template_id`, `audience_id`).
* **`campaign_recipients`**: Individual lead delivery tracking (`status`, `sent_at`, `delivered_at`, `opened_at`, `replied_at`, `bounce_reason`).
* **`templates`**: Dynamic message templates with variable syntax checking (`{{company_name}}`, `{{first_name}}`).

---

## 3. REST API Endpoint Catalog (`/api/v1`)

### 3.1 Authentication & Profile
* `POST /api/v1/auth/login`: Authenticate email/password with brute-force lockout checking.
* `POST /api/v1/auth/logout`: Revoke active session token.
* `GET /api/v1/auth/me`: Fetch current user identity, active workspace, and permissions.
* `POST /api/v1/auth/2fa/verify`: Validate TOTP/2FA challenge.

### 3.2 Scraper Engine
* `GET /api/v1/scrapers`: List all available reconnaissance actors.
* `GET /api/v1/scrapers/{slug}`: Get actor documentation and input schema.
* `POST /api/v1/scrape-jobs`: Queue a new scraper execution run.
* `GET /api/v1/scrape-jobs/{id}`: Poll run state, elapsed duration, and item counters.
* `GET /api/v1/scrape-jobs/{id}/logs`: Fetch execution log lines.
* `POST /api/v1/scrape-jobs/{id}/cancel`: Abort a running scraper actor.
* `GET /api/v1/scrape-jobs/{id}/results`: Stream extracted data rows.

### 3.3 CRM & Lead Management
* `GET /api/v1/leads`: Filterable, paginated lead query (supports full-text search, status, location).
* `GET /api/v1/leads/{id}`: Deep lead record with activity and notes.
* `POST /api/v1/leads`: Manually add or upsert a lead.
* `PATCH /api/v1/leads/{id}`: Update lead status, ownership, or enrichment data.
* `POST /api/v1/leads/import/upload`: Parse and map incoming CSV/XLSX lead file.
* `POST /api/v1/leads/deduplicate/scan`: Trigger fuzzy-match deduplication scan.
* `POST /api/v1/leads/deduplicate/merge`: Merge secondary duplicate leads into primary master record.

### 3.4 Marketing & Outbound
* `GET /api/v1/campaigns`: List active and draft campaigns with real metrics.
* `POST /api/v1/campaigns`: Create a campaign draft.
* `POST /api/v1/campaigns/{id}/validate`: Execute eligibility checks before launch.
* `POST /api/v1/campaigns/{id}/launch`: Transition campaign into delivery queue.
* `POST /api/v1/campaigns/{id}/pause`: Pause an active campaign.

### 3.5 Administration & Security
* `GET /api/v1/teams`: List organization teams and members.
* `POST /api/v1/invitations`: Issue private, single-use employee invitation.
* `GET /api/v1/roles`: View RBAC permission matrix.
* `GET /api/v1/admin/audit`: Query tamper-evident audit logs with filters.
* `GET /api/v1/connections`: List configured third-party integrations and health states.
* `POST /api/v1/connections/test`: Run live probe on configured integration credentials.
