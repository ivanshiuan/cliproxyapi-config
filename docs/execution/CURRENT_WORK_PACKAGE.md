# CURRENT WORK PACKAGE — SEC-001-SPEC-CORRECTION

**Package ID:** SEC-001-SPEC-CORRECTION  
**Date:** 2026-07-29  
**Status:** COMPLETED (documentation only)

---

## Scope

This work package covers SEC-001 remediation specification correction only.

### In Scope

- Correcting evidence state for SEC-001 (local test ≠ production test)
- Defining target security model (PROJECT-SCOPED PRIVATE ASSET)
- Answering 10 data model readiness questions (to the extent possible without codebase access)
- Designing ASSET_SIGNING_SECRET architecture (separate from LP_CODE)
- Specifying signed token payload with 14 verification requirements
- Defining URL signing flow for all canvas use cases
- Specifying cache policy for LP_CODE mode and public mode
- Defining two-phase remediation plan (Phase 1: immediate mitigation; Phase 2: complete signed URL)
- Defining test specifications (signature unit, authorization, cache, canvas regression, production)
- Writing ADR-SEC-001-PRIVATE-ASSET-DELIVERY.md
- Updating ISSUE_REGISTRY.json with corrected state

### Out of Scope

- Modifying any product code (`functions/_lib.js`, `server.js`, or any other source file)
- Creating any git commits
- Pushing to any remote
- Deploying to any environment (preview or production)
- Starting work on SEC-002
- Starting work on SEC-ZHIPU-KEY-001 or SEC-ASSET-SSRF-001
- Implementing Phase 1 or Phase 2
- Migrating existing R2 objects

---

## Deliverables

| File | Status |
|---|---|
| `docs/execution/SEC-001-ROOT-CAUSE.md` | COMPLETE |
| `docs/execution/SEC-001-REMEDIATION-PLAN.md` | COMPLETE |
| `docs/execution/ISSUE_REGISTRY.json` | COMPLETE |
| `docs/execution/TEST_EVIDENCE.md` | COMPLETE |
| `docs/execution/EXECUTION_LOG.md` | COMPLETE |
| `docs/execution/CURRENT_WORK_PACKAGE.md` | COMPLETE (this file) |
| `docs/governance/ADR-SEC-001-PRIVATE-ASSET-DELIVERY.md` | COMPLETE |

---

## Constraints Observed

| Constraint | Observed |
|---|---|
| No product code modified | YES |
| No commit created | YES |
| No push | YES |
| No deployment | YES |
| SEC-002 not started | YES |
| API key migration not started | YES |
| bypassPermissions not used | YES |
| LP_CODE not used as signing secret | YES (prohibited in spec) |
| Production test not claimed from local test | YES (corrected) |

---

## Blockers Identified

| Blocker | Affected Phase | Resolution Path |
|---|---|---|
| Target codebase inaccessible (lovart-plus not in accessible repos) | All code-specific questions marked UNKNOWN | Ivan must authorize access to `buff-hotpot-system` or provide the correct repo name |
| R2 key schema unknown | Phase 2 | Codebase inspection of `functions/_lib.js` key generation logic |
| Canvas persistence format unknown | Phase 2 | Codebase inspection of canvas save/load logic |
| Data model readiness: UNKNOWN | Phase 2 | Answers to Section 2 Q1–Q5 in Remediation Plan |
| Founder Decisions FD-001 through FD-005 | Phase 2 | Ivan's decisions required before Phase 2 design can be finalized |

---

## Next Authorized Work Package

**SEC-001-PHASE1-IMPLEMENTATION** — requires:
1. Ivan to authorize implementation (product code changes allowed)
2. Target repo accessible in this session
3. Phase 1 test plan reviewed and accepted
