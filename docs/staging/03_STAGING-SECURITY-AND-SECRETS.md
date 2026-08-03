# STAGING-SECURITY-AND-SECRETS

---

## Secret Inventory

The following secrets are required. None must appear in: git history, PR
descriptions, CI logs, terminal stdout, error responses, evidence files, or
browser network logs.

| Secret | Where Used | Classification |
|---|---|---|
| `ODOO_API_KEY` | `restaurant_api` -> Odoo JSON-RPC `X-Odoo-Apikey` | HIGH |
| `ODOO_ADMIN_PASSWORD` | Odoo master password (DB management) | HIGH |
| `svc_staging_password` | Odoo service account internal password (never sent by restaurant_api) | MEDIUM |
| `PG_ODOO_PASSWORD` | PostgreSQL `odoo_staging` user password | HIGH |
| `PG_ADMIN_PASSWORD` | PostgreSQL `postgres` user password | HIGH |
| `restaurant_api DATABASE_URL` | SQLAlchemy async DSN | HIGH |
| `restaurant_api SECRET_KEY` | JWT signing | HIGH |

---

## Secret Storage Strategy — By Option

### Option A / B (Self-Hosted VM)

**Layer 1 — Files on VM, never in git:**
```
/opt/odoo-staging/
+-- config/
|   +-- odoo.conf              chmod 640 root:odoo  (contains db_password)
|   +-- .credentials.env       chmod 600 root:root  (admin password)
|   +-- odoo_api_key.txt       chmod 600 root:root
+-- .env.staging               chmod 600 root:root  (non-secret Odoo connection params)
```

```
/home/app/restaurant_api/
+-- .env                       chmod 600 app:app    (ODOO_API_KEY + DB DSN + SECRET_KEY)
+-- [never committed]
```

**Layer 2 — Environment injection at startup:**
```yaml
# docker-compose.staging.yml
services:
  odoo:
    env_file:
      - ./config/.credentials.env   # loaded at container start, not baked into image
```

**Layer 3 — For production-grade staging (recommended):**

Use cloud secret manager (GCP Secret Manager, AWS Secrets Manager, or
HashiCorp Vault) to store all HIGH-classified secrets. The VM retrieves
them at startup via service account IAM, never stores them as files.

---

## Network Isolation

```
Internet
  |
  v
Caddy (port 443) --- HTTPS --- restaurant_api clients
  |
  +-- /api/*  -> restaurant_api (port 8000, internal)
  |
  +-- [Odoo NOT exposed to internet --- internal only]
       |
       v
   Odoo (port 8069, bind 127.0.0.1 only)
       |
       v
   PostgreSQL (port 5432, bind 127.0.0.1 / managed PG VPC only)
```

**Critical rule:** Odoo staging port MUST NOT be exposed to the public
internet. `restaurant_api` calls Odoo on `127.0.0.1:8069` (same VM,
Option A) or on VPC-internal hostname (Option B).

If Odoo admin UI access is needed for Ivan:

- SSH tunnel: `ssh -L 18069:127.0.0.1:8069 staging-vm` then access
  `http://localhost:18069`
- OR: Caddy path-based proxy with HTTP Basic Auth + IP allowlist (Ivan's
  IP only)
- NOT: public DNS record pointing to Odoo port

---

## Service Account Isolation (enforced by client.py — Layer 1)

The `svc-restaurant-api-staging` account receives:

- `account.group_account_user` (Accounting)
- `base.group_partner_manager` (Contact Creation)
- Login disabled for web UI (set `active=False` on the portal login if
  Odoo edition allows, or restrict by IP via Nginx)

The account does NOT receive:

- Administration or Settings groups
- Purchase Administrator
- Payment creation rights
- Bank/cash journal access

This is enforced redundantly at Layer 1 (client.py
`enforce_operation_policy`) and Layer 2 (Odoo ACL). Both must be in place
before staging acceptance.

---

## Secret Rotation Policy

| Secret | Rotation Trigger | Procedure |
|---|---|---|
| `ODOO_API_KEY` | Quarterly / on personnel change | Generate new key in Odoo settings -> update `.env` on VM -> restart `restaurant_api` -> verify health check |
| `PG_ODOO_PASSWORD` | Quarterly | Change in PG + `odoo.conf` -> restart Odoo container |
| `ODOO_ADMIN_PASSWORD` | On admin access (Ivan only) | Odoo Settings -> Users -> Admin |
| `restaurant_api SECRET_KEY` | On breach | Invalidates all sessions — coordinate downtime |

---

## What the Sandbox Proved (Security Posture Baseline)

From evidence run `sbx20260730100528`:

- `service_account_is_admin: False`
- `api_key_in_repr: False` (key never leaks in `__repr__`)
- `all_blocked: True` (all forbidden ops blocked before network egress)
- `all_zero_egress: True` (forbidden ops emit zero bytes to Odoo)
- `auto_post_gate: enforced_draft_only`
- `payment_created: False`

These must be re-verified at staging acceptance (see Acceptance Checklist).
