# QBIT GROWTH OS — TESTING AND ACCEPTANCE PLAN
**Company:** QbitPro India Pvt Ltd.  
**Standard:** Never report an integration or feature as complete without executed and passing automated tests.

---

## 1. Testing Strategy & Levels

```
                     ┌──────────────────────────┐
                     │    End-to-End Tests      │  Full user journeys: Login -> Run Scraper
                     │  (Browser & API flows)   │  -> Inspect Lead -> Launch Campaign
                     ├──────────────────────────┤
                     │ Integration & RBAC Tests │  Multi-tenant data isolation, role
                     │  (Isolated DB instances) │  boundaries, API responses, rate limits
                     ├──────────────────────────┤
                     │   Provider Health Probes │  Real handshake against third-party APIs
                     │  (Contract verification) │  (Google, Apify, WhatsApp, SMTP)
                     ├──────────────────────────┤
                     │    Unit Tests & Logic    │  Schema validation, deduplication ladder,
                     │   (Pytest test suite)    │  Argon2id hashing, template variables
                     └──────────────────────────┘
```

---

## 2. Test Execution Matrix

### 2.1 Identity, Session & RBAC Testing
- `test_auth_login_success`: Valid user login sets secure session and returns identity.
- `test_auth_brute_force_lockout`: 5 consecutive failed attempts trigger account lockout.
- `test_session_revocation`: `tokens_revoked_before` invalidates older active JWT tokens immediately.
- `test_rbac_role_boundaries`:
  - `CEO` can view all organization metrics and audit logs.
  - `Marketing Manager` can create and launch campaigns, but cannot modify RBAC roles.
  - `Marketing Executive` can run scrapers and edit assigned leads, but cannot delete audit logs.
  - `Researcher` has read-only access to leads and intelligence tools.

### 2.2 Scraper & Actor Platform Testing
- `test_actor_input_schema_validation`: Invalid input payloads reject with `422 Unprocessable Entity`.
- `test_scrape_job_state_machine`: Verifies transitions: `PENDING` ➡️ `RUNNING` ➡️ `COMPLETED` / `FAILED`.
- `test_scrape_job_cancellation`: Active worker receives cancellation signal and terminates gracefully.
- `test_scrape_log_streaming`: Verifies real-time persistence of console logs without truncation.
- `test_provider_disconnected_behavior`: Unconfigured provider reports `NOT_CONNECTED` and cleanly prevents execution.

### 2.3 CRM, Leads & Deduplication Testing
- `test_lead_creation_and_provenance`: Extracted records persist source URL, scraper slug, and timestamp.
- `test_lead_deduplication_exact_match`: Identical domain or phone prevents duplicate row creation.
- `test_lead_deduplication_fuzzy_match`: Similar company names in same city flag for merge review.
- `test_csv_importer_column_mapping`: Correctly parses varied CSV formats and creates lead records.

### 2.4 Marketing & Messaging Testing
- `test_email_template_variable_sanitization`: Prevents HTML injection while substituting safe tags (`{{company_name}}`).
- `test_smtp_health_probe`: Validates SMTP server connectivity and authentication.
- `test_whatsapp_waba_health_probe`: Validates token and WABA phone ID status.
- `test_unsubscribe_public_flow`: Public unsubscribe link adds recipient to suppression list.

---

## 3. Automated Test Execution Commands

```bash
# Run complete test suite in isolated test database
.\backend\venv\Scripts\python.exe -m pytest tests/ -v

# Run RBAC and security tests
.\backend\venv\Scripts\python.exe -m pytest tests/test_rbac.py tests/test_security.py -v

# Run Scraper and Actor platform tests
.\backend\venv\Scripts\python.exe -m pytest tests/test_actor_platform.py tests/test_scrapers.py -v

# Run Lead and CRM tests
.\backend\venv\Scripts\python.exe -m pytest tests/test_leads.py -v
```

---

## 4. Acceptance Criteria & Certification
A feature is certified complete **only** when:
1. All relevant backend endpoints are deployed and covered by automated tests.
2. The exact approved Stitch UI is wired with real API data and responsive states (loading, error, empty, data).
3. Role-based permissions are enforced server-side.
4. Audit logs are recorded for all create, update, and delete actions.
5. External providers are verified through real health probes or clearly labeled `NOT CONNECTED`.
