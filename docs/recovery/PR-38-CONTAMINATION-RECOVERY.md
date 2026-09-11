# PR #38 Contamination Recovery

**Date:** 2026-07-29
**Repository:** `ivanshiuan/cliproxyapi-config`
**Incident class:** Pull-request scope contamination (non-destructive recovery)

---

## 1. What happened

PR #38 (*Odoo 財務後台整合：權限白名單 + 冪等同步 + 獨立認證*) was opened at
2026-07-29T07:47:17Z from branch `claude/odoo-inventory-integration-s4olur`
containing exactly 6 commits, all within the approved Odoo back-office
integration scope.

After PR creation, two commits **unrelated to the Odoo scope** were pushed to
the same branch, entering PR #38's diff:

| SHA | Subject | Scope violation |
|---|---|---|
| `c4f1a7c23f989730042677ab674ef1f2768a9bf3` | docs(sec-001): add complete remediation specification for unauthorized asset read | SEC-001 asset-signing spec for the external `lovart-plus` project (7 files under `docs/execution/`, `docs/governance/`) |
| `2163fef944c3d46d11323d9045822ac97d04a7b4` | chore(evidence): add ZHIPU fix Phase 1 scope reconciliation evidence | Zhipu/lovart-plus evidence (4 files under `Evidence/Zhipu-Fix/`) |

A read-only incident audit (same date) classified both commits
`UNRELATED_TO_PR_38` and issued the verdict `PR_38_CONTAMINATED`.

## 2. Recovery approach (Option A — no history rewrite)

- The contaminated branch `claude/odoo-inventory-integration-s4olur` was
  **not reset, not force-pushed, not modified**.
- PR #38 was **not closed and not modified**; it is preserved as incident
  evidence.
- A clean branch `recovery/odoo-integration-clean-pr38` was created from the
  last clean Odoo commit `dfc18c60b03c8eff5178cd2aa8d9b74536f99b3c`.
- A replacement draft PR was opened from the recovery branch to the original
  base `claude/autonomous-resttech-enterprise-oW9jp`.

## 3. Contamination exclusion proof

Executed on branch `recovery/odoo-integration-clean-pr38`
(HEAD `dfc18c60b03c8eff5178cd2aa8d9b74536f99b3c`):

- `git merge-base --is-ancestor c4f1a7c… HEAD` → **not an ancestor** (PASS)
- `git merge-base --is-ancestor 2163fef… HEAD` → **not an ancestor** (PASS)
- All 11 contaminating files absent from both the git tree and the working
  directory (4 × `Evidence/Zhipu-Fix/*`, 6 × `docs/execution/*`,
  1 × `docs/governance/ADR-SEC-001-*`) (PASS)
- Commits relative to `origin/claude/autonomous-resttech-enterprise-oW9jp`
  (`2778d163ba086114198d298ffec877978c70c647`, the PR base) are exactly the
  6 original Odoo commits:
  `aaacea6`, `1695949`, `25c807c`, `7caee33`, `67e9e0f`, `dfc18c6` (PASS)

## 4. Quality gates (re-executed on the recovery branch)

See `PR-38-RECOVERY-TEST-RESULTS.md` for the full re-run results. Summary:
lint clean · pyright 0/0 · pytest 681 passed / 1 pre-existing environmental
failure / 1 skipped · Odoo suite 45/45 · alembic at head + no drift ·
db-smoke pass · secret scan 0 hits.

## 5. Disposition

- **PR #38:** preserved open (draft) as incident evidence. Do not merge.
- **Replacement PR:** draft, must not be merged until the commander (Ivan)
  reviews and the original PR #38 is dispositioned.
- **Contaminated commits:** remain reachable on the original branch only;
  excluded from the replacement PR by construction.
