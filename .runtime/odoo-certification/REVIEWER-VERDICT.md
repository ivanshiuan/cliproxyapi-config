# Independent Reviewer Verdict

Reviewer: separate read-only subagent (did not author the code; ran git/pytest/
python only; created/edited/deleted nothing; no commits).

All 10 mandated checks **PASS**:
1. Critical tests re-run: 45/45 passed (33+8+4), exit 0.
2. Permission boundary hands-on: all 5 forbidden ops raised (unlink,
   payment create, action_register_payment, res.users read, ir.config_parameter
   read); both allowed ops passed.
3. Duplicate sync: query fence + [src:] find-before-create confirmed in code and
   exercised by rerun + crash-replay test (1 move, same id, 0 duplicates).
4. Payment: no write path exists; account.payment read-only by construction;
   zero network traffic on denial.
5. Unlink: FORBIDDEN_METHODS, checked first, not configurable; no call site.
6. Migrations: DSL-only (no raw SQL), sane FKs, symmetric downgrades;
   alembic check clean.
7. Secrets: key absent from repr/str/logs; only fake fixtures in diff; .env untracked.
8. Commit scope: 4 commits, 16 files, all in scope; no unrelated files; no
   deleted tests (2 removed lines = updated table-count assertion).
9. Report-vs-log: claimed counts match raw logs; E2E evidence explicitly labeled
   SIMULATED, no live-Odoo claim.
10. Hostile read: no policy bypass (only _rpc callers are _authenticate and
    _execute_kw whose first statement is the policy); no key logging;
    ODOO_ALLOW_AUTO_POST env flip alone cannot post (fail-safe).
    Minor note (fixed in closing commit): client.py docstring overstated the
    auto-post flag; corrected to state code change is also required.

**VERDICT: CONDITIONALLY_CERTIFIED** — condition: before production go-live,
run the documented deployment checklist against a real Odoo sandbox
(scoped service user authenticates; one full sync produces a draft vendor
bill; one re-run proves zero duplicates). No live Odoo was reachable in this
environment (EXTERNAL_DEPENDENCY_BLOCKED).

---

## Post-verdict remediation note — 2026-07-29 (NOT a reviewer statement)

After this verdict, an **implementer** remediation (branch
`recovery/odoo-integration-clean-pr38`) addressed the independent re-review's
sandbox blockers: mandatory supplier `partner_id` binding on vendor bills,
search-based create recovery (no blind re-send on lost responses, for both
account.move and res.partner), TWD-only currency fence, zero-amount PO skip,
`invoice_date`, tenant-qualified source markers, and a scheduler-registration
test. Implementer gate results live in TEST-RESULTS.txt and RISK-REGISTER.md
(R7–R13).

This note is written by the remediation implementer. It does NOT upgrade or
re-issue the verdict above: **CONDITIONALLY_CERTIFIED stands unchanged**, the
live-Odoo sandbox condition remains unexecuted, and the remediation itself
requires a fresh independent re-review.
