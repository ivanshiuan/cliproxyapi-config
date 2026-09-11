# STAGING-GO-NO-GO — Merge Gate Proposal

---

## Current State (as of 2026-08-01)

| Item | State |
|---|---|
| PR #39 | OPEN, Ready for Review, NOT merged, draft=false |
| Live Sandbox | `LIVE_ODOO_SANDBOX_VERIFIED` (sbx20260730100528) |
| Certification | `FINAL_CERTIFIED_FOR_SINGLE_TENANT_STAGING` |
| Production | NOT AUTHORIZED |
| Merge | NOT AUTHORIZED |
| Release | NOT AUTHORIZED |

---

## Merge Gate Proposal — Two Strategies

### Strategy 1: Merge First, Deploy from main

**Flow:**
```
PR #39 reviewed by Ivan
  -> Ivan merges to main
  -> staging deployment from main
```

**Pros:**

- Clean git history; main stays current
- All future development branches from the merged state
- Standard git workflow

**Cons:**

- Merge before staging deployment means main contains
  untested-in-staging code
- If staging deployment reveals a blocking issue, a new PR/commit is
  needed to fix main

**Condition for this strategy to be safe:** The live sandbox
`sbx20260730100528` evidence constitutes sufficient staging-analog
testing. The deployment risk is configuration (chart of accounts, service
account), not code.

---

### Strategy 2: Deploy from Branch, Merge After Staging Passes

**Flow:**
```
Deploy staging from recovery/odoo-integration-clean-pr38
  -> run acceptance checklist
  -> all items green
  -> Ivan merges PR #39
```

**Pros:**

- Merge only after the exact branch commit is proven on a real
  environment
- No risk of committing to main based on untested deployment

**Cons:**

- Slightly longer before main is updated
- Team must coordinate that main is outdated during staging deployment
  period

---

### RECOMMENDATION: Strategy 2

The code is already certified. The remaining risk is entirely
deployment-side (chart of accounts config, service account provisioning,
AP account mapping). Those risks are addressed by the runbook and
acceptance checklist, not by the code. Deploying from the branch and
merging after the acceptance checklist is fully signed off by Ivan is the
lower-risk path.

**Strategy 2 merge trigger:** All 46 items in Acceptance Checklist
checked green + Ivan signs off.

---

## Pre-Merge Checklist (Ivan)

Before pressing Merge on PR #39, confirm:

- [ ] Staging acceptance checklist fully complete (all 46 items)
- [ ] Restore drill completed and result documented
- [ ] AP account `2100` verified in staging Odoo (not `211000`)
- [ ] Service account non-admin confirmed
- [ ] At least 5 successful PO syncs in staging with zero duplicates
- [ ] `make full-check` green on staging host
- [ ] `STAGING-ENVIRONMENT.json` evidence file saved and SHA256 recorded
- [ ] Ivan has reviewed the diff one final time (code has not changed
      since certification)

---

## Production Authorization Gate (separate from merge gate)

Merge of PR #39 authorizes code to main. It does NOT authorize production
deployment.

**Production authorization requires a separate, explicit decision** after:

1. Single-tenant staging has run for a defined observation period
   (recommended: 2 weeks)
2. At least 50 real purchase orders successfully synced in staging
3. Accounting team has verified vendor bill journal entries against chart
   of accounts
4. Backup restore drill completed and documented
5. Separate production deployment plan written and approved (analogous to
   this document, but for production)
6. All CRITICAL risks (R15, R16) verified mitigated in production network
   topology
7. Full Taiwan tax configuration strategy (Phase 2 or formal Phase 1
   acceptance) decided

---

## Final Decision Block

```
RECOMMENDED STAGING ARCHITECTURE:  Option B — VM + Managed PostgreSQL
ESTIMATED MONTHLY COST:             NTD 900 (Hetzner + Render) / NTD 2,660 (GCP)
MERGE STRATEGY:                     Strategy 2 — merge after staging acceptance
MERGE AUTHORIZED:                   NO  (awaiting Ivan sign-off after acceptance checklist)
STAGING DEPLOYMENT AUTHORIZED:      YES (this plan is the authorization artifact)
PRODUCTION AUTHORIZED:              NO
RELEASE AUTHORIZED:                 NO
ODOO VERSION LOCK:                  17.0-20260723 (sha256:f836...)
POSTGRES VERSION LOCK:              16 (sha256:33f9...)
BLOCKING OPEN RISKS:                R01, R05, R09, R13, R14 (must close before production)
CURRENT STATUS:                     PLAN COMPLETE — AWAITING EXECUTION
```
