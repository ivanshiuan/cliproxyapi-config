# FINAL-CERTIFICATION-REPORT — Odoo Integration

- Mission run: odoo-cert-20260729T072525Z
- Branch: claude/odoo-inventory-integration-s4olur
- Status: **CONDITIONALLY_CERTIFIED** (independent reviewer; implementer did not self-certify)
- Sole condition / remaining blocker: live-Odoo sandbox validation — impossible in
  this environment (no Odoo instance; secrets/hosts must not be guessed). Deployment
  checklist: ODOO-PERMISSION-MATRIX.md §Layer 2 + REVIEWER-VERDICT.md condition.

## Completion conditions
1. Prior claims independently classified — DONE (CLAIM-VERIFICATION.md: 14/14 VERIFIED, 0 CONTRADICTED)
2. Reproducible clean baseline — DONE (BASELINE-REPORT.md; venv built this session from pyproject; migrations from zero on scratch DB)
3. Minimum-permission architecture documented + tested — DONE (permission matrix, threat model, tool contract; adversarial tests)
4. No generic unrestricted Odoo access — DONE (no execute_kw surface; policy chokepoint; reviewer check 10)
5. Draft vendor bill E2E — DONE vs simulated Odoo JSON-RPC contract server; live = EXTERNAL_DEPENDENCY_BLOCKED (the condition)
6. Re-run produces no duplicate — DONE (rerun + crash-replay + timeout-retry tests; reviewer check 3)
7. Payment / unlink / arbitrary model / settings proven blocked — DONE (reviewer checks 2,4,5; zero egress on denial)
8. Full quality gate — DONE (681 passed / 1 skipped; 1 pre-existing EXTERNAL_DEPENDENCY_BLOCKED live-LINE test; ruff clean; pyright 0; alembic from-zero + downgrade round-trip + no-drift; db smoke; secret scan clean; pip-audit ran — 1 dev-dep advisory in RISK-REGISTER R2)
9. Independent reviewer — CONDITIONALLY_CERTIFIED (condition is the environmental blocker above, not a code defect)
10. Draft PR — see EXECUTION-MANIFEST.json
11. Evidence manifest with hashes — EVIDENCE-SHA256-MANIFEST.txt
12. Open P0/P1 — none (RISK-REGISTER.md: highest open item is P2 = the same external blocker)

## Commits under certification
- aaacea6 docs plan · 1695949 integration layer · 25c807c procurement+sync ·
  7caee33 hardening (policy/retry/dedup/dead-letter/E2E) · 67e9e0f evidence ·
  closing commit: reviewer verdict + manifests + docstring accuracy fix.

---

## Remediation addendum — 2026-07-29 (implementer, post-certification)

Scope: sandbox blockers from the independent re-review of PR #39.

1. **partner_id binding** — `create_vendor_bill(entry, *, partner_id)` requires
   a positive int from `upsert_supplier`; validated before any egress; not
   injectable via PO payload (no such field exists on the input dataclasses).
2. **Safe create retry** — method-aware policy: reads keep bounded retry;
   `create` never blind-resends. On timeout/5xx/malformed-response the client
   searches the unique source marker (`[src:purchase_order:<tenant>:<po>]`) /
   partner ref and reuses the server-committed record; re-create only when the
   search proves absence; all attempts bounded; retry logs carry run_id +
   marker + attempt + action, never the API key.
3. **Mandatory E2E variant** — server commits the move, response lost:
   create sent exactly once, one stored move, recovered id == stored id ==
   `purchase_order.odoo_move_id`. Plus: timeout-before-create, persistent
   timeout (bounded, dead-letter path), application errors never retried,
   malformed 2xx triggers recovery not blind re-create.
4. **TWD guard** — non-TWD refused pre-egress in service and client.
5. **Zero-amount PO** — skipped (no call, no attempt, no dead-letter), audited.
6. **invoice_date** — set to the controlled local receiving date.
7. **Scheduler registration test** — 04:45 Asia/Taipei, id, guards asserted.

Gates (re-run this date): ruff clean · pyright 0/0 · full pytest 697 passed /
1 failed / 1 skipped (sole failure = pre-existing live-LINE broadcast test,
EXTERNAL_DEPENDENCY_BLOCKED by sandbox egress proxy — NOT full green) · Odoo
suite 61/61 · alembic upgrade head + check no-drift · db-smoke PASS · secret
scan 0 true hits.

**Live Odoo sandbox: STILL NOT EXECUTED.** This addendum is an implementer
record; independent re-review of the remediation is required.
