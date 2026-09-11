# CLAIM-VERIFICATION — Odoo Integration Certification

Run: see `mission-state.json`. Method: every claim resolved against `git` objects,
the remote branch, and a fresh execution of the full gate — never trusted from the report.

| # | Claim | Status | Evidence |
|---|-------|--------|----------|
| 1 | commit `aaacea6` exists | **VERIFIED** | sha `aaacea63…59a5`, parent `656ed1f`, 1 file (+245); `diff-evidence/aaacea6.patch` |
| 2 | commit `1695949` exists | **VERIFIED** | sha `1695949…4938`, 5 files (+1239/−6); `diff-evidence/1695949.patch` |
| 3 | commit `25c807c` exists | **VERIFIED** | sha `25c807c…794b`, 8 files (+740/−2); `diff-evidence/25c807c.patch` |
| 4 | branch `claude/odoo-inventory-integration-s4olur` on remote with all 3 commits | **VERIFIED** | `git merge-base --is-ancestor` → all `ON_REMOTE` |
| 5 | `OdooClient` / `StubOdooClient` / `HttpOdooClient` exist | **VERIFIED** | `restaurant_api/integrations/odoo/client.py`; 45 Odoo tests exercise all three |
| 6 | procurement tables (suppliers / purchase_orders / purchase_order_lines) | **VERIFIED** | models + live DB check: 3 tables present after upgrade; 0 after downgrade round-trip |
| 7 | Alembic migration | **VERIFIED** | rev `8307a94acb62`; applied from ZERO on scratch DB `resto_cert`; downgrade→re-upgrade round-trip proven |
| 8 | `odoo_sync_service` | **VERIFIED** | file exists; behaviour re-proven by 9 DB tests (idempotency, error isolation, dead-letter, reconcile) |
| 9 | 04:45 nightly job | **VERIFIED** | `jobs/__init__.py` `CronTrigger(hour=4, minute=45)` id=`odoo_sync` |
| 10 | "661 passed, 1 skipped" | **VERIFIED (with disclosed caveat)** | Baseline re-run: `661 passed, 1 skipped, 1 failed`. The 1 failure is `test_http_segments_and_broadcast` — a **live api.line.me call** blocked by the sandbox egress proxy (403). Classified **EXTERNAL_DEPENDENCY_BLOCKED**: file predates the branch (last commit `a8dd147`, ancestor of the branch base), imports nothing from `integrations/odoo`, and its deterministic MockTransport equivalent passes in `tests/test_line_integration.py`. The original claim was made with this one test deselected and said so. |
| 11 | ruff clean | **VERIFIED** | `ruff check devswarm restaurant_api tests scripts` → "All checks passed!" |
| 12 | pyright 0 | **VERIFIED** | `pyright` → 0 errors, 0 warnings |
| 13 | alembic no-drift | **VERIFIED** | `alembic check` → "No new upgrade operations detected." on both dev and scratch DBs |
| 14 | db smoke ok | **VERIFIED** | `scripts/smoke_db.py` exit 0 |

**Caveat worth stating**: repo-wide `ruff format --check` fails on **102 legacy files**.
That is PRE-EXISTING (the project's own `full-check` gate has never included the
formatter) and untouched by this branch; all 13+ branch-changed files are format-clean.
Not repaired here — reformatting 102 unrelated files is forbidden churn.

**Conclusion**: every prior claim independently classified; none CONTRADICTED.
