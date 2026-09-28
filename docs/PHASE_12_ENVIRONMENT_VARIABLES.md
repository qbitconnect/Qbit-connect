# Phase 12 — QBIT Connect Production Environment Variables Reference

This document provides a comprehensive catalog of all runtime configuration parameters used by the QBIT Connect web application (`qbit-api`) and background daemon (`qbit-worker`).

---

## 1. Core Platform & Security Configuration

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `QBIT_ENV` | `str` | Yes | `production` | Public | Environment mode (`development`, `test`, `staging`, `production`). In `production`, mock providers and insecure secrets are rejected at boot. |
| `QBIT_SECRET_KEY` | `str` | Yes | *None* | **SECRET** | Primary cryptographic secret key used for session signing, JWT creation, and state hashing. Must be at least 32 characters (48+ recommended). |
| `QBIT_COOKIE_SECURE` | `bool` | No | `true` | Public | Enforces the `Secure` flag on HTTP cookies. Must be `true` in production to prevent cookie transmission over plaintext HTTP. |
| `QBIT_SESSION_TTL_MINUTES` | `int` | No | `480` | Internal | Lifespan of user login sessions before requiring re-authentication (8 hours). |
| `QBIT_LOGIN_MAX_FAILED_ATTEMPTS` | `int` | No | `10` | Internal | Number of sequential failed login attempts before a user account is locked to prevent credential brute-forcing. |
| `QBIT_LOGIN_LOCKOUT_MINUTES` | `int` | No | `15` | Internal | Duration in minutes an account remains locked following failed attempt exhaustion. |
| `QBIT_RATE_LIMIT_LOGIN_PER_MIN` | `int` | No | `10` | Internal | Maximum login requests permitted per minute per IP address. |
| `QBIT_CORS_ORIGINS` | `str` | No | `""` | Public | Comma-separated list of allowed CORS browser origins (e.g. `https://app.qbit.com`). |
| `QBIT_LOG_LEVEL` | `str` | No | `INFO` | Public | Application logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

---

## 2. Database & Cache Connectivity

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `str` | Yes | *None* | **SECRET** | SQLAlchemy async connection string. Production requires PostgreSQL (e.g. `postgresql+asyncpg://user:pass@host:5432/qbit`). SQLite is permitted in test only. |
| `REDIS_URL` | `str` | No | `""` | **SECRET** | Redis connection URI (`redis://:password@host:6379/0`). Used for distributed rate limiting, queue coordination, and analytics caching. |
| `POSTGRES_DB` | `str` | Yes (Compose) | `qbit` | Internal | Database name inside the `qbit-db` container. |
| `POSTGRES_USER` | `str` | Yes (Compose) | `qbit` | Internal | Database username inside the `qbit-db` container. |
| `POSTGRES_PASSWORD` | `str` | Yes (Compose) | *None* | **SECRET** | Database password inside the `qbit-db` container. |
| `REDIS_PASSWORD` | `str` | Yes (Compose) | *None* | **SECRET** | Password required to authenticate with Redis 7. |

---

## 3. Storage & Backup Settings

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `QBIT_DATA_DIR` | `Path` | No | `./qbit-data` | Internal | Root filesystem directory for persistent storage (mapped to `/qbit-data` in containers). |
| `QBIT_EXPORT_DIR` | `Path` | No | `$DATA/exports`| Internal | Target directory for generated CSV/XLSX lead exports. |
| `QBIT_LOG_DIR` | `Path` | No | `$DATA/logs` | Internal | Target directory for persistent structured log files. |
| `QBIT_BACKUP_DIR` | `Path` | No | `$DATA/backups`| Internal | Destination directory for automated GFS backup snapshots. |
| `QBIT_BACKUP_SCHEDULE_HOURS` | `int` | No | `24` | Internal | Cadence for automated database and filesystem backup cycles (hours). Set to `0` to disable. |
| `QBIT_BACKUP_RETENTION_DAILY`| `int` | No | `14` | Internal | Retain the most recent N daily backup archives. |
| `QBIT_BACKUP_RETENTION_WEEKLY`| `int` | No | `8` | Internal | Retain the most recent N weekly backup archives. |
| `QBIT_BACKUP_RETENTION_MONTHLY`| `int` | No | `6` | Internal | Retain the most recent N monthly backup archives. |
| `QBIT_DISK_WARN_PERCENT` | `int` | No | `85` | Internal | Threshold percentage of filesystem disk utilization that triggers an operations alert. |

---

## 4. Background Worker & Scraper Engine

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `QBIT_WORKER_DRAIN_SECONDS` | `int` | No | `30` | Internal | Grace period in seconds to pause/checkpoint active jobs before graceful container shutdown. |
| `QBIT_WORKER_LEASE_SECONDS` | `int` | No | `120` | Internal | Lease timeout before an unresponsive worker's jobs are reclaimed by surviving workers. |
| `QBIT_WORKER_MAX_CONCURRENT_JOBS` | `int` | No | `2` | Internal | Maximum parallel scrape executions per worker instance. |
| `QBIT_SCRAPER_CONCURRENCY` | `int` | No | `4` | Internal | Network concurrency per individual scrape job. |
| `QBIT_SCRAPER_RPS_PER_HOST` | `float` | No | `1.0` | Internal | Maximum requests per second per target domain (anti-ban and polite crawling). |
| `QBIT_SCRAPER_ALLOW_PRIVATE_TARGETS` | `bool` | No | `false` | Public | Strict SSRF guard. MUST be `false` in production to prevent crawlers probing internal networks. |
| `QBIT_MAPS_PROVIDER` | `str` | No | `none` | Public | Maps integration provider (`none`, `outscraper`, `http`, `mock`). |
| `QBIT_MAPS_PROVIDER_URL` | `str` | No | `""` | Public | Endpoint URL when using custom licensed HTTP maps provider. |
| `QBIT_MAPS_PROVIDER_API_KEY` | `str` | No | `""` | **SECRET** | Credentials for the chosen maps provider. |

---

## 5. Webhooks & Communication Providers

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `EMAIL_WEBHOOK_SECRET` | `str` | Yes | *None* | **SECRET** | Shared secret used to verify inbound HMAC signatures on transactional email webhooks. |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | `str` | No | `""` | **SECRET** | Handshake token configured in Meta App Dashboard for webhook verification. |
| `WHATSAPP_APP_SECRET` | `str` | No | `""` | **SECRET** | Meta App Secret used to compute SHA-256 HMAC signatures on incoming WhatsApp webhook payloads. |
| `EMAIL_PROVIDER` | `str` | No | `smtp` | Public | Email delivery backend (`smtp` or `api`). |
| `SMTP_HOST` | `str` | No | `""` | Public | Outbound SMTP server hostname. |
| `SMTP_PORT` | `int` | No | `587` | Public | Outbound SMTP port. |
| `SMTP_SECURITY` | `str` | No | `STARTTLS` | Public | Transport security (`STARTTLS`, `TLS`, `NONE`). |
| `SMTP_USERNAME` | `str` | No | `""` | Public | SMTP authentication username. |
| `SMTP_PASSWORD` | `str` | No | `""` | **SECRET** | SMTP authentication password. |

---

## 6. AI Agents & Sales Intelligence (Phase 9)

| Variable | Type | Required? | Default | Sensitivity | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `AI_PROVIDER` | `str` | No | `openai` | Public | LLM provider backend (`openai`, `anthropic`, `google`, `mock`). `mock` is forbidden in production. |
| `AI_API_KEY` | `str` | No | `""` | **SECRET** | Provider API key. Never logged or exposed via REST API responses. |
| `AI_BASE_URL` | `str` | No | `https://api.openai.com/v1` | Public | Endpoint URL (must use HTTPS; SSRF guards reject private IPs). |
| `AI_DEFAULT_MODEL` | `str` | No | `gpt-4o-mini`| Public | Model used for extraction, classification, and scoring tasks. |
| `AI_REASONING_MODEL` | `str` | No | `gpt-4o` | Public | Model used for complex lead enrichment and sales brief generation. |
| `AI_MAX_TOKENS` | `int` | No | `4096` | Public | Maximum completion token ceiling per request. |
| `AI_REQUEST_TIMEOUT` | `int` | No | `60` | Public | Timeout in seconds for LLM completions. |
| `AI_MAX_COST_PER_RUN` | `float` | No | `0.50` | Public | Hard budget cap in USD per agent execution run. |
| `QBIT_AI_ALLOW_MOCK_PROVIDER` | `bool` | No | `false` | Public | Must remain `false` in production. Enabling in production causes runtime configuration rejection. |
