# STAGING-DEPLOYMENT-RUNBOOK — 12-Step Pipeline

**Prerequisites:**

- VM provisioned and SSH access confirmed
- Docker installed on VM (with `mirror.gcr.io` access confirmed:
  `docker pull mirror.gcr.io/library/hello-world`)
- Managed PG created (Option B) or PG will run in Docker (Option A)
- DNS record created: `staging.odoo.example.com` -> VM IP (or use IP
  directly for staging)
- SSH key added to VM
- Ivan's IP address known (for Odoo admin UI allowlist)

---

## Step 1: Repository and File Structure

```bash
ssh staging-vm
sudo mkdir -p /opt/odoo-staging/{config,data/filestore,addons}
sudo chown -R ubuntu:ubuntu /opt/odoo-staging
cd /opt/odoo-staging
```

Clone only the config files (never the `.env` files — those are injected
separately):
```bash
# docker-compose.staging.yml and odoo.conf go here (from PR #39 template)
# Secrets are injected in Step 4 — never from git
```

---

## Step 2: Pull Docker Images via mirror.gcr.io

```bash
# Pull pinned digests (from sandbox evidence)
docker pull mirror.gcr.io/library/odoo@sha256:f83602ecb7c5dfab85402bd10ece785bb2a883dd8e97e6884cacf4566dd4daa1
docker pull mirror.gcr.io/library/postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20

# Tag for compose reference
docker tag mirror.gcr.io/library/odoo@sha256:f83602ecb7c5dfab85402bd10ece785bb2a883dd8e97e6884cacf4566dd4daa1 odoo-staging:17.0
docker tag mirror.gcr.io/library/postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20 postgres-staging:16
```

Verification: `docker images | grep staging` — both present.

---

## Step 3: Write docker-compose.staging.yml

```yaml
# /opt/odoo-staging/docker-compose.staging.yml
services:
  db:
    image: postgres-staging:16  # pinned in Step 2
    container_name: odoo-staging-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: odoo_staging
      POSTGRES_DB: resto_staging
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    secrets:
      - pg_password
    volumes:
      - staging_pgdata:/var/lib/postgresql/data
    networks:
      - staging_net
    # Option A only: expose on localhost for pg_dump
    ports:
      - "127.0.0.1:15432:5432"

  odoo:
    image: odoo-staging:17.0    # pinned in Step 2
    container_name: odoo-staging
    restart: unless-stopped
    depends_on:
      - db
    volumes:
      - staging_odoodata:/var/lib/odoo
      - ./config/odoo.conf:/etc/odoo/odoo.conf:ro
      - ./addons:/mnt/extra-addons:ro
    env_file:
      - ./config/.credentials.env
    networks:
      - staging_net
    ports:
      - "127.0.0.1:18069:8069"   # NEVER expose to 0.0.0.0

volumes:
  staging_pgdata:
  staging_odoodata:

networks:
  staging_net:
    driver: bridge

secrets:
  pg_password:
    file: ./config/pg_password.txt
```

---

## Step 4: Inject Secrets (Never from Git)

```bash
# On staging VM only, typed interactively:
read -rs PG_PASS && echo "$PG_PASS" > /opt/odoo-staging/config/pg_password.txt
chmod 600 /opt/odoo-staging/config/pg_password.txt

read -rs ADMIN_PASS && cat > /opt/odoo-staging/config/.credentials.env <<CEOF
ADMIN_PASSWD=$ADMIN_PASS
CEOF
chmod 600 /opt/odoo-staging/config/.credentials.env

# odoo.conf (contains db_host, db_port, db_user — no passwords if using
# Docker secrets)
cat > /opt/odoo-staging/config/odoo.conf <<CEOF
[options]
addons_path = /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
data_dir = /var/lib/odoo
db_host = db
db_port = 5432
db_user = odoo_staging
db_name = resto_staging
log_level = info
without_demo = all
CEOF
chmod 644 /opt/odoo-staging/config/odoo.conf
```

---

## Step 5: Start Services and Initialize Database

```bash
cd /opt/odoo-staging
docker compose -f docker-compose.staging.yml up -d db
sleep 5  # PG init

docker compose -f docker-compose.staging.yml up -d odoo
# Wait for Odoo init (~60-120 seconds for first run)
until curl -sf http://127.0.0.1:18069/web/health; do sleep 5; done
echo "Odoo ready"
```

---

## Step 6: Configure Odoo via Admin UI (SSH Tunnel)

```bash
# On local machine:
ssh -L 18069:127.0.0.1:18069 staging-vm &

# Browser: http://localhost:18069
# Complete setup wizard:
#   - Country: Taiwan
#   - Currency: TWD
#   - Company name: [Restaurant Name] Staging
#   - Install: Accounting module
#   - Chart of accounts: Taiwan (l10n_tw) or manual
```

---

## Step 7: Configure Chart of Accounts

Via Odoo Admin UI or admin RPC:

1. Create/verify account `1310` — 存貨 (Inventory Asset)
2. Create/verify account `1360` — 進項稅額 (Input VAT Asset)
3. Create/verify account `2100` — 應付帳款 (Accounts Payable) — set as
   default payable
4. Create journal `PUR` — type: Purchase, default account: 1310

See `04_STAGING-ACCOUNTING-CONFIG.md` for details.

---

## Step 8: Provision Service Account

```python
#!/usr/bin/env python3
"""Admin provisioning script — NOT part of restaurant_api runtime."""
import xmlrpc.client

URL = "http://127.0.0.1:18069"
DB = "resto_staging"
ADMIN_USER = "admin"
# Load ADMIN_PASS from .credentials.env — never hardcode
import os
with open("/opt/odoo-staging/config/.credentials.env") as f:
    ADMIN_PASS = f.read().split("=", 1)[1].strip()

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
uid = common.authenticate(DB, ADMIN_USER, ADMIN_PASS, {})

models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

# Resolve required group IDs
accounting_group = models.execute_kw(
    DB, uid, ADMIN_PASS, "ir.model.data", "search_read",
    [[["module", "=", "account"], ["name", "=", "group_account_user"]]],
    {"fields": ["res_id"], "limit": 1},
)[0]["res_id"]

partner_mgr_group = models.execute_kw(
    DB, uid, ADMIN_PASS, "ir.model.data", "search_read",
    [[["module", "=", "base"], ["name", "=", "group_partner_manager"]]],
    {"fields": ["res_id"], "limit": 1},
)[0]["res_id"]

# Create service account
svc_id = models.execute_kw(
    DB, uid, ADMIN_PASS, "res.users", "create",
    [{
        "name": "Restaurant API Staging",
        "login": "svc-restaurant-api-staging",
        "email": "svc-staging@internal",
        "groups_id": [(4, accounting_group), (4, partner_mgr_group)],
    }],
)
print(f"SVC_USER_ID={svc_id}")
print("Now generate an API key in Odoo Settings -> Users -> API Keys")
```

Save the generated API key to
`/opt/odoo-staging/config/odoo_api_key.txt` (chmod 600).

---

## Step 9: Configure restaurant_api Staging .env

```bash
# On restaurant_api staging host (NOT in git):
cat > /home/app/restaurant_api/.env <<CEOF
ODOO_URL=http://127.0.0.1:18069
ODOO_DB=resto_staging
ODOO_USERNAME=svc-restaurant-api-staging
ODOO_API_KEY=$(cat /opt/odoo-staging/config/odoo_api_key.txt)
ODOO_ALLOW_AUTO_POST=false
DATABASE_URL=postgresql+asyncpg://app:REDACTED@localhost:5432/resto_staging_app
SECRET_KEY=REDACTED
CEOF
chmod 600 /home/app/restaurant_api/.env
```

---

## Step 10: Run Integration Smoke Test

```bash
cd /home/app/restaurant_api
.venv/bin/pytest tests/test_odoo_integration.py tests/test_odoo_e2e_contract.py -v
```

All tests must pass. If any fail, DO NOT proceed to step 11.

---

## Step 11: Run make full-check

```bash
cd /home/app/restaurant_api
make full-check
# ruff check + pyright + pytest + alembic check + smoke
```

All gates green. Any failure = STOP, investigate, fix, re-run.

---

## Step 12: Run Staging Acceptance Test

Execute the full acceptance checklist from
`08_STAGING-ACCEPTANCE-CHECKLIST.md`. All 34 items must pass before
declaring staging READY.
