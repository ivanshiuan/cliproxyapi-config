# PR #38 Recovery — Re-executed Quality Gate Results

**Date:** 2026-07-29
**Branch:** `recovery/odoo-integration-clean-pr38`
**HEAD:** `dfc18c60b03c8eff5178cd2aa8d9b74536f99b3c`
**Baseline (PR base):** `origin/claude/autonomous-resttech-enterprise-oW9jp` @ `2778d163ba086114198d298ffec877978c70c647`

All gates below were re-executed on this branch on 2026-07-29 (not carried
over from any prior run).

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check devswarm restaurant_api tests scripts` | **All checks passed** |
| Formatter (informational) | `ruff format --check devswarm tests` | 51 files would be reformatted under freshly installed ruff 0.16.0 (unpinned latest). Formatting is **not** part of the repo's enforced `full-check` gate; no product code was modified during recovery. |
| Type check | `pyright` (1.1.411) | **0 errors, 0 warnings, 0 informations** |
| Full test suite | `pytest tests/ -q` | **681 passed, 1 failed, 1 skipped (683 collected)** |
| Odoo suite (unit + policy + DB integration + E2E contract) | `pytest tests/ -k odoo` | **45 passed, 0 failed** (`test_odoo_integration.py`, `test_odoo_sync_service.py`, `test_odoo_e2e_contract.py`) |
| Migration upgrade | `alembic upgrade head` | At head, no errors |
| Migration no-drift | `alembic check` | **No new upgrade operations detected** |
| DB smoke | `python scripts/smoke_db.py` | **PASS** — insert + select + FK relationships end-to-end |
| Secret scan | High-confidence pattern grep over the full 46-file diff vs base (`sk-ant-`, AWS `AKIA`, private-key blocks, `ghp_`, `github_pat_`, Slack `xox*`) | **0 hits** |

## The single test failure — pre-existing and environmental

`tests/routers/test_growth_endpoints.py::test_http_segments_and_broadcast`
fails with `httpx.ProxyError: 403 Forbidden`: the test performs a real LINE
broadcast HTTP call, which the remote sandbox's egress proxy blocks.

Evidence that it is unrelated to the Odoo work:

- The file `tests/routers/test_growth_endpoints.py` was last modified in
  commit `a8dd147`, which is on the **base branch** — none of the 6 Odoo
  commits touch it (`git log <base>..HEAD -- tests/routers/test_growth_endpoints.py`
  is empty).
- The same environmental failure was documented in the original PR #38
  description ("被沙盒 proxy 擋").
- Mock-equivalent coverage of the same code path is green.

## Test totals cross-check

- `pytest --collect-only`: 683 tests collected.
- Progress-marker tally of the run log: 681 `.` + 1 `F` + 1 `s` = 683. Consistent.

---

## Remediation re-run — 2026-07-29 (sandbox-blocker fixes on this branch)

All gates re-executed after the `fix(odoo)` remediation commit (not carried
over from the table above):

| Gate | Result |
|---|---|
| ruff check | All checks passed |
| pyright | 0 errors, 0 warnings |
| Full pytest | **697 passed, 1 failed, 1 skipped (699 collected)** |
| Sole failure | `tests/routers/test_growth_endpoints.py::test_http_segments_and_broadcast` — EXTERNAL_DEPENDENCY_BLOCKED (live LINE call, sandbox egress proxy 403); pre-existing on base, untouched by this diff. NOT full green. |
| Odoo suite (unit + policy + sync + E2E + scheduler test) | **61 passed, 0 failed** |
| alembic upgrade head | at head, OK |
| alembic check | No new upgrade operations detected |
| db-smoke | PASS |
| Secret scan | 0 true high-confidence hits (1 self-referential match: the literal pattern list in this document) |

Remediation content: supplier partner_id binding (pre-egress validated,
non-injectable), search-based create recovery for lost responses (account.move
and res.partner; mandatory server-created-response-lost E2E variant), TWD-only
fence, zero-amount PO skip, invoice_date, tenant-qualified source marker,
scheduler-registration test. Live Odoo sandbox: still NOT executed.
