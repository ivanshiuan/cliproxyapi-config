# STAGING-COST-MODEL

**Assumptions:**

- 1 USD = 32 NTD (conservative)
- Taiwan region (GCP asia-east1 / AWS ap-northeast-1)
- Single tenant, ~200 POs/month at launch
- Staging runs continuously (not ephemeral — evidence persistence required)

---

## Option A: Single VM + Docker Compose

| Component | Spec | Provider | NTD/month |
|---|---|---|---|
| VM | 4 vCPU / 8 GB / 80 GB SSD | GCP e2-standard-4 (Taiwan) | ~3,200 |
| VM | 4 vCPU / 8 GB / 80 GB SSD | AWS t3.xlarge (ap-northeast-1) | ~3,500 |
| VM | 4 vCPU / 8 GB / 80 GB SSD | Hetzner CX31 (Europe) | ~640 |
| Egress (1 GB/month) | — | Any | ~32-96 |
| Backup storage (100 GB/month) | — | Object storage | ~64 |
| **Total A (GCP)** | | | **~3,360** |
| **Total A (Hetzner)** | | | **~760** |

Hetzner is geographically distant from Taiwan; latency to `restaurant_api`
(if deployed in Taiwan) adds ~180 ms round-trip. Acceptable for staging
reads; vendor bill creates are async. If `restaurant_api` is also on the
same Hetzner VM, latency is internal.

---

## Option B: VM + Managed PostgreSQL (RECOMMENDED)

| Component | Spec | Provider | NTD/month |
|---|---|---|---|
| VM (Odoo only) | 2 vCPU / 4 GB / 40 GB SSD | GCP e2-standard-2 | ~1,600 |
| VM (Odoo only) | 2 vCPU / 4 GB / 40 GB SSD | Hetzner CX21 | ~320 |
| Managed PG 16 | 1 vCPU / 1 GB / 20 GB SSD | GCP Cloud SQL | ~960 |
| Managed PG 16 | 1 vCPU / 1 GB / 20 GB SSD | Render PG | ~480 |
| Managed PG 16 | 1 vCPU / 1 GB / 20 GB SSD | Supabase (free tier) | ~0 (limited PITR) |
| Egress + backup | — | — | ~100 |
| **Total B (GCP only)** | | | **~2,660** |
| **Total B (Hetzner + Render)** | | | **~900** |
| **Total B (Hetzner + Supabase)** | | | **~420** |

---

## Option C: Odoo.sh — REJECTED

| Component | NTD/month |
|---|---|
| Odoo.sh Starter (1 staging branch) | ~800 |
| Ops overhead (SaaS mismatch) | Significant (unquantified) |
| **Total C** | **~800 + risk** |

Rejected — see Architecture Decision Document.

---

## Recommended Budget

- **Minimum viable (Hetzner + Supabase free):** ~NTD 420/month
- **Recommended (Hetzner + Render Managed PG):** ~NTD 900/month
- **GCP all-in (production-parity security):** ~NTD 2,660/month

For a staging environment that must be proven before a production contract,
the Hetzner + Render option at ~NTD 900/month is the cost-optimized
recommendation. GCP all-in is the recommendation if production will also
run on GCP (staging-to-production parity is worth the premium).

---

## One-Time Setup Costs

| Item | Estimate |
|---|---|
| Odoo initial configuration (Ivan time) | 2-4 hours |
| Chart of accounts Taiwan setup | 1-2 hours |
| Service account provisioning | 30 minutes (automated via runbook) |
| SSL certificate (Let's Encrypt via Caddy) | Free |
| `restaurant_api` staging `.env` | 30 minutes |
| End-to-end acceptance test run | 2-3 hours |
