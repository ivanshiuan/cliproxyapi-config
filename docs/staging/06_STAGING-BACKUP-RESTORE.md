# STAGING-BACKUP-RESTORE

---

## Objectives

| Metric | Target | Rationale |
|---|---|---|
| RPO (Recovery Point Objective) | 24 hours | Staging data is reproducible; full PITR not required |
| RTO (Recovery Time Objective) | 4 hours | Staging downtime acceptable; no SLA |
| Retention | 7 days | Enough to roll back a bad migration or bad data import |
| Restore drill | Required before production authorization | Untested backup = no backup |

---

## What to Back Up

| Component | Backup Target | Method |
|---|---|---|
| Odoo PostgreSQL DB | Full daily dump | `pg_dump` or managed PG snapshot |
| Odoo filestore | `/opt/odoo-staging/data/filestore/` | rsync to object storage |
| `restaurant_api` PostgreSQL DB | Full daily dump | `pg_dump` / managed PG snapshot |
| Odoo config (non-secret) | Git (already tracked) | N/A |
| Secret values | Password manager / secret manager | Separate from backup |

---

## Backup Implementation — Option A (Full Docker)

PostgreSQL backup cron (runs at 02:00 Asia/Taipei = 18:00 UTC):
```bash
#!/bin/bash
# /opt/scripts/backup-pg.sh
set -euo pipefail
BACKUP_DIR=/opt/backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"

docker exec odoo-staging-db pg_dump \
  -U odoo_staging \
  -d resto_staging \
  --format=custom \
  --compress=9 \
  > "$BACKUP_DIR/odoo_db_$(date +%H%M%S).pgdump"

docker exec restaurant-api-db pg_dump \
  -U app \
  -d resto_staging_app \
  --format=custom \
  --compress=9 \
  > "$BACKUP_DIR/api_db_$(date +%H%M%S).pgdump"

# Upload to object storage (GCS/S3)
gsutil rsync -r "$BACKUP_DIR" gs://resto-staging-backups/$(date +%Y%m%d)/

# Prune backups older than 7 days
find /opt/backups -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
```

Filestore backup:
```bash
rsync -az /opt/odoo-staging/data/filestore/ \
  gs://resto-staging-backups/filestore/$(date +%Y%m%d)/
```

---

## Backup Implementation — Option B (Managed PG)

- GCP Cloud SQL: Enable automated backups (daily, 7-day retention) in
  console
- Render PG: Enable point-in-time recovery in dashboard
- Odoo filestore: Same rsync script above (VM-side only)
- `restaurant_api` DB: Same managed PG automated backup

---

## Restore Procedure (Mandatory Drill Before Production)

Drill scenario: Simulate a corrupted database. Restore to a point 24 hours
before current time.

### Step 1: Stop services
```bash
docker compose -f docker-compose.staging.yml stop odoo restaurant_api
```

### Step 2: Restore Odoo DB from backup
```bash
# Drop and recreate
docker exec odoo-staging-db psql -U postgres \
  -c "DROP DATABASE IF EXISTS resto_staging;"
docker exec odoo-staging-db psql -U postgres \
  -c "CREATE DATABASE resto_staging OWNER odoo_staging;"

# Restore from dump
docker exec -i odoo-staging-db pg_restore \
  -U odoo_staging \
  -d resto_staging \
  --clean --if-exists \
  < /opt/backups/YYYYMMDD/odoo_db_HHMMSS.pgdump
```

### Step 3: Restore filestore (if needed)
```bash
rsync -az gs://resto-staging-backups/filestore/YYYYMMDD/ \
  /opt/odoo-staging/data/filestore/
```

### Step 4: Restart and verify
```bash
docker compose -f docker-compose.staging.yml start odoo restaurant_api
curl http://127.0.0.1:18069/web/health  # Odoo health
curl https://staging.api.example.com/health/ready  # restaurant_api health
```

### Step 5: Verify data integrity
```bash
# Confirm most recent PO sync still present
python3 -c "
from restaurant_api.database import get_sessionmaker
import asyncio
async def check():
    async with get_sessionmaker()() as s:
        result = await s.execute(
            'SELECT count(*) FROM purchase_orders WHERE odoo_move_id IS NOT NULL'
        )
        print('synced POs:', result.scalar())
asyncio.run(check())
"
```

### Drill pass criteria

- Odoo web UI accessible after restore
- Service account API key still valid
- At least 1 purchase order with `odoo_move_id` visible in restored DB
- New PO sync creates a new vendor bill in restored Odoo
- `make full-check` passes

**The restore drill MUST be completed and documented before production
authorization.**
