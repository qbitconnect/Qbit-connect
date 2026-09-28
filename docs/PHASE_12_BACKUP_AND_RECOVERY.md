# Phase 12 — QBIT Connect Backup, Disaster Recovery & Rollback Runbook

## 1. Objectives & RPO / RTO Guarantees

- **Recovery Point Objective (RPO):** < 24 hours (maximum acceptable data loss in catastrophic unrecoverable hardware failure; reduced to < 1 hour with managed WAL archiving).
- **Recovery Time Objective (RTO):** < 30 minutes from backup restoration invocation to full system availability.
- **Retention Policy (GFS Scheme):**
  - **Daily:** Retain 14 daily snapshots.
  - **Weekly:** Retain 8 weekly snapshots.
  - **Monthly:** Retain 6 monthly snapshots.

---

## 2. Automated Backup Subsystem (`qbit-worker`)

The unified worker includes an automated background backup scheduler controlled by `QBIT_BACKUP_SCHEDULE_HOURS` (default: 24h).

Each scheduled cycle executes:
1. **Database Dump:** Calls PostgreSQL engine utility to dump the relational database schema and data into a compressed, timestamped file.
2. **Metadata & Configuration Snapshot:** Captures the current migration head, Git commit SHA, and non-sensitive configuration manifest into `backup-manifest.json`.
3. **Storage Archive:** Compresses `/qbit-data/exports` and uploaded assets into tar.gz.
4. **Checksum Verification:** Generates SHA-256 integrity digests for all output archives.
5. **Retention Pruning:** Automatically purges expired snapshots according to GFS retention limits.

---

## 3. Manual Backup Procedures

### 3.1 On-Demand PostgreSQL Database Backup
To take an immediate database snapshot before applying a major schema change or application upgrade:

```bash
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="/var/lib/qbit-data/backups"
mkdir -p "$BACKUP_DIR"

# Execute pg_dump inside the database container
docker compose exec -T qbit-db pg_dump -U qbit -d qbit -F c -b -v \
  > "$BACKUP_DIR/db_manual_${TIMESTAMP}.dump"

# Generate SHA256 checksum
sha256sum "$BACKUP_DIR/db_manual_${TIMESTAMP}.dump" > "$BACKUP_DIR/db_manual_${TIMESTAMP}.dump.sha256"

echo "Database backup complete: db_manual_${TIMESTAMP}.dump"
```

### 3.2 Persistent File Storage Backup
```bash
tar -czvf "$BACKUP_DIR/storage_manual_${TIMESTAMP}.tar.gz" \
  --exclude="cache" \
  --exclude="backups" \
  -C /var/lib/qbit-data .

sha256sum "$BACKUP_DIR/storage_manual_${TIMESTAMP}.tar.gz" > "$BACKUP_DIR/storage_manual_${TIMESTAMP}.tar.gz.sha256"
```

---

## 4. Disaster Recovery & Restoration Procedures

### 4.1 Restoring PostgreSQL from a Dump
When recovering to a fresh server or rolling back after a corrupt migration:

```bash
# 1. Stop the application and worker containers to prevent connection lockups
docker compose stop qbit-api qbit-worker

# 2. Drop existing database and recreate clean target database
docker compose exec qbit-db psql -U qbit -d postgres -c "DROP DATABASE IF EXISTS qbit;"
docker compose exec qbit-db psql -U qbit -d postgres -c "CREATE DATABASE qbit OWNER qbit;"

# 3. Restore the database dump using pg_restore
docker compose exec -T qbit-db pg_restore -U qbit -d qbit -v \
  < /var/lib/qbit-data/backups/db_manual_YYYYMMDD_HHMMSS.dump

# 4. Run Alembic upgrade head to ensure all migrations are current
docker compose run --rm qbit-api alembic upgrade head

# 5. Restart application services
docker compose up -d qbit-api qbit-worker
```

### 4.2 Restoring Filesystem Assets
```bash
tar -xzvf /var/lib/qbit-data/backups/storage_manual_YYYYMMDD_HHMMSS.tar.gz -C /var/lib/qbit-data/
```

---

## 5. Application Rollback Runbooks

### 5.1 Reverting to Previous Container Image
If a newly deployed container image introduces application regressions:

```bash
# 1. Identify previous stable image tag
docker images qbit-connect-api

# 2. Update image tag in docker-compose.yml or export tag variable
export QBIT_IMAGE_TAG=0.1.9

# 3. Re-deploy with zero database degradation
docker compose up -d --no-deps qbit-api qbit-worker
```

### 5.2 Database Migration Rollback (`alembic downgrade`)
Every migration in `backend/alembic/versions` contains a strictly verified `downgrade()` implementation.

To revert a single schema version:
```bash
# Check current revision
docker compose exec qbit-api alembic current

# Downgrade by 1 revision
docker compose exec qbit-api alembic downgrade -1

# Verify status
docker compose exec qbit-api alembic current
```

---

## 6. Disaster Recovery Drill Schedule

| Exercise | Frequency | Verification Method |
| :--- | :--- | :--- |
| Database dump integrity test | Weekly | Automated SHA-256 verification |
| Staging restore dry-run | Monthly | Automated test restoring dump into staging Postgres instance |
| Full server cutover simulation | Quarterly | Rebuild stack on independent cloud instance from S3/backup archive |
