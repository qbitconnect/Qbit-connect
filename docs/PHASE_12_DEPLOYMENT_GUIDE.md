# Phase 12 — QBIT Connect Production Deployment Guide

This guide provides concrete, step-by-step procedures for deploying QBIT Connect to production infrastructure.

---

## 1. System Requirements & Prerequisites

### 1.1 Minimum Compute Requirements
- **CPU:** 4 vCPUs (Intel Xeon / AMD EPYC / Graviton 3).
- **RAM:** 8 GB RAM minimum (16 GB recommended for concurrent scrapers and daily analytics aggregation).
- **Storage:** 50 GB SSD / NVMe (persistent root volume for OS, database, and `/qbit-data`).
- **OS:** Ubuntu 22.04 LTS / Debian 12 / Rocky Linux 9.

### 1.2 Required Software Packages
- Docker Engine 24.0+ & Docker Compose v2.20+
- OpenSSL & Curl
- Python 3.12+ (if deploying directly to VM systemd without containers)
- Nginx 1.24+ or Traefik 3.0+

---

## 2. Option A: Docker Compose Deployment (Recommended for Single-Host VM)

### Step 1: Clone Repository & Create Data Directory
```bash
git clone https://github.com/your-org/qbit-connect.git /opt/qbit-connect
cd /opt/qbit-connect

# Create persistent storage directories with non-root ownership
sudo mkdir -p /var/lib/qbit-data/{exports,logs,backups,scrapes,cache}
sudo chown -R 10001:10001 /var/lib/qbit-data
```

### Step 2: Configure Production Environment Variables
Generate cryptographic keys and copy the production configuration template:

```bash
# Generate high-entropy 48-byte URL-safe base64 secrets
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

cp .env.example .env

# Edit .env and supply actual values:
sed -i "s/change-me-long-random-secret-key-at-least-48-chars/$SECRET_KEY/g" .env
sed -i "s/change-me-strong-postgres-password/$DB_PASS/g" .env
sed -i "s/change-me-strong-redis-password/$REDIS_PASS/g" .env
sed -i "s/change-me-webhook-secret-min-32-chars/$WEBHOOK_SECRET/g" .env
sed -i "s|./qbit-data|/var/lib/qbit-data|g" .env

# Verify file permissions
chmod 600 .env
```

### Step 3: Build Container Images
```bash
docker compose build --pull
```

### Step 4: Run Database Migrations & Initial Seed
Run the Alembic migration suite in a transient container before starting the web servers:

```bash
# Apply all 18 database migrations
docker compose run --rm qbit-api alembic upgrade head

# Seed roles, permissions, and create initial CEO/Super Admin user
docker compose run --rm qbit-api python -m app.cli seed \
  --admin-email admin@yourdomain.com \
  --admin-password "YourInitialSecurePassword123!" \
  --admin-name "System Administrator"
```

### Step 5: Launch Services
```bash
docker compose up -d
```

Verify service status:
```bash
docker compose ps
docker compose logs -f qbit-api
docker compose logs -f qbit-worker
```

---

## 3. Ingress & TLS Setup with Nginx & Let's Encrypt

### Step 1: Install Nginx & Certbot
```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx
```

### Step 2: Request SSL Certificate
```bash
sudo certbot certonly --standalone -d app.yourdomain.com --agree-tos -m ops@yourdomain.com
```

### Step 3: Configure Nginx Virtual Host
Copy the provided Nginx configuration file:
```bash
sudo cp /opt/qbit-connect/deploy/nginx-qbit.conf /etc/nginx/sites-available/qbit.conf

# Edit server_name and SSL certificate paths
sudo sed -i "s/qbit.example.com/app.yourdomain.com/g" /etc/nginx/sites-available/qbit.conf
sudo sed -i "s|/etc/ssl/certs/qbit.crt|/etc/letsencrypt/live/app.yourdomain.com/fullchain.pem|g" /etc/nginx/sites-available/qbit.conf
sudo sed -i "s|/etc/ssl/private/qbit.key|/etc/letsencrypt/live/app.yourdomain.com/privkey.pem|g" /etc/nginx/sites-available/qbit.conf

# Enable virtual host and test config
sudo ln -s /etc/nginx/sites-available/qbit.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 4. Option B: Cloud Kubernetes Deployment (EKS / GKE)

For container orchestration on Kubernetes, separate Deployments are used for `qbit-api` and `qbit-worker`:

1. **qbit-api Deployment:**
   - Replicas: 2+ with Horizontal Pod Autoscaler (HPA) targeting 70% CPU utilization.
   - Probes:
     - Liveness: `HTTP GET /health/live` on port 8000 (`initialDelaySeconds: 15`, `periodSeconds: 10`).
     - Readiness: `HTTP GET /health/ready` on port 8000 (`initialDelaySeconds: 20`, `periodSeconds: 15`).
2. **qbit-worker Deployment:**
   - Replicas: 1 (or statically scaled depending on queue backend configuration).
   - Probes:
     - Liveness: `ExecAction` checking heartbeat timestamp in `/qbit-data/cache/worker-heartbeat.json`.
3. **Storage:**
   - PersistentVolumeClaim (PVC) backed by AWS EBS (`gp3`) or GCP Persistent Disk mounted at `/qbit-data`.
4. **Database & Cache:**
   - AWS RDS PostgreSQL 16 (Multi-AZ) or Google Cloud SQL.
   - AWS ElastiCache for Redis or Google Cloud Memorystore.

---

## 5. Post-Deployment Verification

Execute the automated production readiness verification script:
```bash
docker compose exec qbit-api python ../scripts/verify_production_readiness.py
```

Expected result:
```
============================================================
 PRE-FLIGHT AUDIT SUMMARY: 21 PASSED, 0 FAILED
============================================================
```

Navigate to `https://app.yourdomain.com/login` and log in with your seeded administrator credentials.
