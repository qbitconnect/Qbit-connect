# Phase 12 — QBIT Connect Production Go-Live Checklist

Use this checklist prior to pointing production DNS to the live infrastructure. Every item must be verified and signed off by the Operations / Engineering leads.

---

## Phase A: Pre-Flight Environment & Security Audit

- [ ] **Secret Keys & Entropy:**
  - `QBIT_SECRET_KEY` generated with high entropy (`>= 48` random characters). Default or insecure placeholder keys eliminated.
  - `POSTGRES_PASSWORD` and `REDIS_PASSWORD` set to strong, unique passwords.
  - `.env` file permissions set to `chmod 600` on the host machine.
- [ ] **Environment Strictness:**
  - `QBIT_ENV` explicitly set to `production`.
  - `QBIT_AI_ALLOW_MOCK_PROVIDER=false` (verified boot failure if mock provider is attempted).
  - `QBIT_MARKETING_ALLOW_MOCK_PROVIDER=false`.
- [ ] **SSRF & Anti-Scraper Guards:**
  - `QBIT_SCRAPER_ALLOW_PRIVATE_TARGETS=false` to block crawler probing of private subnets (`10.0.0.0/8`, `192.168.0.0/16`, `127.0.0.1`, cloud metadata `169.254.169.254`).
- [ ] **Cookie Security:**
  - `QBIT_COOKIE_SECURE=true` verified. Session cookies configured with `HttpOnly`, `SameSite=Lax`, and `Secure`.
- [ ] **Automated Pre-Flight Script:**
  - Executed `python scripts/verify_production_readiness.py` with 100% checks passing.

---

## Phase B: Infrastructure & Database Readiness

- [ ] **Database Migrations:**
  - `alembic upgrade head` executed successfully against production PostgreSQL.
  - All 18 migration revisions (`0001` through `0018`) verified in `alembic_version` table.
- [ ] **Initial User Seed:**
  - Administrator seeded with secure email and complex temporary password.
  - Forced password reset confirmed upon first administrator login.
- [ ] **Storage & Directory Mounts:**
  - Persistent volume `/qbit-data` mounted with read/write permissions for UID 10001 (`qbit` user).
  - Subdirectories `exports/`, `logs/`, `backups/`, `scrapes/`, `cache/` initialized.
  - Host disk space monitored (`df -h`) with at least 20 GB free space.

---

## Phase C: Networking, DNS, and TLS Verification

- [ ] **DNS Records Configured:**
  - A/AAAA record pointing `app.yourdomain.com` to edge reverse proxy / load balancer IP.
  - CAA record published allowing Let's Encrypt / DigiCert.
- [ ] **TLS Certificate & Ciphers:**
  - Valid TLS 1.3 certificate active with automatic renewal timer enabled (`certbot renew --dry-run`).
  - Strict HTTPS redirection active (HTTP port 80 permanently redirects 301 to port 443).
- [ ] **Security Headers Verified:**
  - `curl -I https://app.yourdomain.com` confirms presence of:
    - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
    - `X-Frame-Options: DENY`
    - `X-Content-Type-Options: nosniff`
    - `Content-Security-Policy`

---

## Phase D: Worker & Background Jobs Verification

- [ ] **Worker Liveness & Heartbeat:**
  - `qbit-worker` container running.
  - Heartbeat timestamp updating every ≤ 30s in `/qbit-data/cache/worker-heartbeat.json`.
- [ ] **Graceful Shutdown Testing:**
  - Sent `SIGTERM` to worker; verified log message: `Drain grace period elapsed; pausing in-flight jobs`.
- [ ] **Scheduled Backup Verification:**
  - Daily backup scheduler initialized (`QBIT_BACKUP_SCHEDULE_HOURS=24`).

---

## Phase E: Communications & Webhook Verification

- [ ] **Email Provider (SMTP / API):**
  - Outbound connection test passed via `/api/v1/email/connections/test`.
  - SPF, DKIM, and DMARC DNS records published for sending domains.
  - `EMAIL_WEBHOOK_SECRET` configured; verified invalid signatures receive HTTP 401.
- [ ] **WhatsApp Cloud API:**
  - Webhook handshake verified with `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
  - Inbound webhook HMAC SHA-256 validation active.

---

## Phase F: Day-1 Operations & Monitoring

- [ ] **Health Endpoint Monitoring:**
  - Synthetic monitoring (UptimeRobot / Datadog / Prometheus) polling:
    - `GET https://app.yourdomain.com/health/live` (1-minute interval, alert on non-200).
    - `GET https://app.yourdomain.com/health/ready` (5-minute interval).
- [ ] **Log Ingestion:**
  - JSON structured logs collected to central aggregator.
- [ ] **Alerting Contacts:**
  - PagerDuty / Slack operational notification channels tested.
- [ ] **Sign-Off:**
  - Deployment Lead: ______________________ Date: ______________
  - Engineering Lead: ____________________ Date: ______________
