# STAGING-ARCHITECTURE-DECISION

**Date:** 2026-08-01
**Status:** PROPOSED — awaiting Ivan decision
**Scope:** Single restaurant tenant, staging environment only
**Authorizations:** Production NO / Merge NO / Release NO

---

## Context

PR #39 has reached `FINAL_CERTIFIED_FOR_SINGLE_TENANT_STAGING`. The live
sandbox run (`sbx20260730100528`) confirmed:

- Real draft vendor bills created on Odoo 17 JSON-RPC
- Balanced, partner-bound, Odoo-generated payable
- Zero duplication on plain rerun and stamp-loss replay
- Permission contract enforced: zero egress on all forbidden ops

The sandbox ran on `localhost` only with a fresh throwaway database. Staging
must use a network-reachable Odoo instance with a real chart of accounts,
real AP account mapping, and a real service account that persists across
deploys.

---

## Option A — Single VM + Full Docker Compose

```
VM (4 vCPU / 8 GB / 80 GB SSD)
+-- docker-compose.staging.yml
|   +-- postgres:16          (volume: staging_pgdata)
|   +-- odoo:17.0            (volume: staging_odoodata, staging_addons)
|   +-- [restaurant_api]     (optional: can run from local machine)
+-- Caddy / nginx (HTTPS termination, localhost-only Odoo port)
```

Image pull via `mirror.gcr.io` (confirmed reachable; Docker Hub CDN blocked
by proxy policy):
```
mirror.gcr.io/library/postgres:16
mirror.gcr.io/library/odoo:17.0
```

Database name: `resto_staging` (never `prod`, `production`, `live`)

**Pros:**

- Identical topology to sandbox; zero new unknowns
- Everything in one place; easy to snapshot VM for restore drill
- Cheapest compute option
- Odoo image digest pin carries forward directly from sandbox evidence

**Cons:**

- PostgreSQL not managed; backup requires custom cron or pg_dump script
- VM failure = both Odoo and PG down simultaneously
- No HA; single point of failure (acceptable for staging)
- PG major version upgrade requires manual steps

**Risk:** MEDIUM — acceptable for staging, NOT acceptable for production

---

## Option B — VM + Managed PostgreSQL

```
VM (2 vCPU / 4 GB / 40 GB SSD)
+-- docker-compose.staging.yml
|   +-- odoo:17.0            (volume: staging_odoodata)
|   +-- [restaurant_api]     (optional)
+-- Caddy (HTTPS)

Managed PG 16 (cloud provider: GCP Cloud SQL, AWS RDS, or Render PG)
+-- database: resto_staging
    user: odoo_staging
    password: <in secret manager>
```

Odoo config change:
```ini
db_host = <managed-pg-host>
db_port = 5432
db_user = odoo_staging
db_password = <from secret>
```

**Pros:**

- Automated daily snapshots, PITR from provider
- PG patching managed by provider
- VM can be rebuilt without touching DB
- Cleaner secret separation (DB password in secret manager, not VM disk)

**Cons:**

- Higher cost (managed PG adds NTD 500-1,500/month)
- Extra latency between VM and managed PG (typically < 5ms on same region;
  acceptable)
- Odoo connection to external PG requires SSL config

**Risk:** LOW-MEDIUM — recommended for staging transitioning toward
production

---

## Option C — Odoo.sh

```
Odoo.sh SaaS platform
+-- Branch: staging
+-- Odoo version: 17.0
+-- Database: managed by Odoo.sh
+-- Custom modules: restaurant_api does NOT push custom Odoo modules
```

**Pros:**

- Zero ops burden for Odoo infra
- Automatic backups, staging branches, CI/CD baked in
- Odoo.sh has native staging branch concept

**Cons:**

- FUNDAMENTAL MISMATCH: Our integration model is API-key JSON-RPC.
  Odoo.sh provides an Odoo SaaS hosting environment but our
  `restaurant_api` lives entirely outside it. The only benefit would be
  managed Odoo hosting.
- Cannot pin image digest (Odoo.sh controls upgrades)
- REST API key generation requires Odoo.sh web UI admin — does not fit
  non-interactive provisioning
- Cost: USD 24.90/month (starter) — payable to Odoo SA, not locally
  controlled
- Odoo.sh restricts outbound connections; `restaurant_api` calling inbound
  to Odoo.sh is fine but their firewall rules may complicate the setup
- No staging/production parity benefit since the real production Odoo
  instance will not be on Odoo.sh in a typical self-hosted Taiwan
  deployment

**RECOMMENDATION: REJECTED** for this project

---

## Architecture Decision Matrix

| Criterion | Option A (Single VM) | Option B (VM + Managed PG) | Option C (Odoo.sh) |
|---|---|---|---|
| Cost (NTD/month) | ~1,200-2,000 | ~2,500-4,000 | ~800 + ops complexity |
| Backup quality | Custom script (medium) | Provider PITR (high) | Managed (high) |
| Staging-to-prod parity | High | High | Low |
| Secret isolation | Medium | High | Low (no digest pin) |
| Image digest pin | Yes | Yes | NO |
| Ops complexity | Low | Low-Medium | High (SaaS mismatch) |
| Unknown risk | Low | Low | HIGH |
| **Verdict** | **Viable** | **RECOMMENDED** | **REJECTED** |

---

## RECOMMENDED ARCHITECTURE: Option B — VM + Managed PostgreSQL

Rationale: The extra NTD ~1,500/month for managed PG buys daily PITR
(critical before any accounting data enters the system), automated
patching, and clean secret separation. For a staging environment that will
be used to prove the AP integration before production, the backup quality
difference is material — a botched restore drill with `pg_dump` is worse
than not running one at all. The VM remains small (2 vCPU/4 GB) and cheap;
Odoo is the memory consumer, not the VM itself.

Option A remains acceptable if cost is the binding constraint. The runbook
in Document 7 covers both; the only difference is the PostgreSQL connection
source.
