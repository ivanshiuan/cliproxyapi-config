# Odoo Single-Tenant Staging — Deployment Plan Index

**Created:** 2026-08-03
**Status:** PLAN COMPLETE — AWAITING EXECUTION
**PR:** #39 (`recovery/odoo-integration-clean-pr38`, HEAD `0cef8fa`)
**Certification:** `FINAL_CERTIFIED_FOR_SINGLE_TENANT_STAGING`

---

| # | Document | Purpose |
|---|---|---|
| 01 | [Architecture Decision](01_STAGING-ARCHITECTURE-DECISION.md) | Compare 3 options (Single VM / VM + Managed PG / Odoo.sh) |
| 02 | [Cost Model](02_STAGING-COST-MODEL.md) | NTD cost estimates per option |
| 03 | [Security and Secrets](03_STAGING-SECURITY-AND-SECRETS.md) | Isolation, secret management, network topology |
| 04 | [Accounting Config](04_STAGING-ACCOUNTING-CONFIG.md) | AP account mapping, chart of accounts, journal setup |
| 05 | [Tax Strategy](05_STAGING-TAX-STRATEGY.md) | Phase 1 (EXPLICIT_INPUT_VAT_LINE) vs Phase 2 |
| 06 | [Backup and Restore](06_STAGING-BACKUP-RESTORE.md) | RPO/RTO, retention, mandatory restore drill |
| 07 | [Deployment Runbook](07_STAGING-DEPLOYMENT-RUNBOOK.md) | 12-step pipeline from VM to acceptance |
| 08 | [Acceptance Checklist](08_STAGING-ACCEPTANCE-CHECKLIST.md) | 46 acceptance criteria across 9 sections |
| 09 | [Risk Register](09_STAGING-RISK-REGISTER.md) | 18 risks classified and tracked |
| 10 | [Go / No-Go](10_STAGING-GO-NO-GO.md) | Merge gate, pre-merge checklist, production gate |

---

## Decision Required from Ivan

1. **Architecture:** Option A or B? (recommended: B)
2. **Provider:** GCP / Hetzner+Render / Hetzner+Supabase?
3. **Merge strategy:** Strategy 1 or 2? (recommended: 2)

```
PRODUCTION AUTHORIZED:  NO
MERGE AUTHORIZED:       NO
RELEASE AUTHORIZED:     NO
```
