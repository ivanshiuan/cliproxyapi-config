# RISK-REGISTER

| ID | Sev | Risk | Status / Mitigation |
|---|---|---|---|
| R1 | P2 | Odoo-side ACL/record rules cannot be live-verified (no Odoo instance in env) | OPEN-EXTERNAL. Client-side layer fully tested; deployment checklist in ODOO-PERMISSION-MATRIX §Layer 2 MUST be executed at first real deployment. Not a P0/P1: the code layer alone already blocks every forbidden operation pre-egress. |
| R2 | P3 | pip-audit: pytest 8.4.2 PYSEC-2026-1845 (fix: 9.0.3) — dev-only dependency, not shipped | OPEN. Upgrading pytest major is out of mission scope (pins `<9.0`); recommend a separate chore PR. |
| R3 | P3 | Repo-wide formatter drift (102 legacy files) | PRE-EXISTING, untouched. Recommend one-shot `ruff format` chore PR + adding format-check to full-check. |
| R4 | P3 | Live-LINE test permanently red in sandboxed CI-like envs | PRE-EXISTING. Recommend marking it with a `live` pytest marker skipped unless LINE creds present. |
| R5 | P3 | `ODOO_ALLOW_AUTO_POST=true` would let the nightly job post to the ledger without review | BY DESIGN (documented one-time policy switch); default false; posting additionally needs `post=True` which the job never passes — flipping env alone changes nothing until code opts in. |
| R6 | P4 | Odoo taxes unconfigured — VAT carried as explicit input-VAT line | Documented in E2E evidence; revisit when a real Odoo CoA exists. |

No P0/P1 open.
