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

## Remediation addendum — 2026-07-29 (sandbox-blocker fixes, implementer)

| ID | Sev | Risk | Status / Mitigation |
|---|---|---|---|
| R7 | P2 | Vendor bill created without partner_id (unattributed drafts) | **CLOSED**: `create_vendor_bill` now requires a positive-int `partner_id` sourced from `upsert_supplier`'s return; validated pre-egress; PO payload cannot inject one (no such field exists on PurchaseBill/JournalEntry). Adversarial tests added. |
| R8 | P2 | `create` blind-retry on timeout/5xx could duplicate a draft when the server committed but the response was lost | **CLOSED**: method-aware retry — reads keep bounded retry; `create` (account.move AND res.partner) never blind-resends: transient failure → search the unique source marker/ref → reuse if found, re-create only when provably absent; totals bounded. Mandatory server-created-response-lost E2E variant added. |
| R9 | P3 | Non-TWD amounts could book unconverted | **CLOSED**: TWD-only fence in service (PO.currency_code) and client (`ALLOWED_CURRENCIES`), refused before any egress. |
| R10 | P3 | Zero-amount PO burned failure attempts into dead-letter | **CLOSED**: explicitly skipped (no Odoo call, no attempt bump), audit-logged, surfaced as `SyncReport.skipped`. |
| R11 | P3 | invoice_date absent on vendor bills | **CLOSED**: `invoice_date` set to the controlled local receiving date (same as accounting date), never caller input. |
| R12 | P3 | Source marker was PO id only | **CLOSED**: marker now `[src:purchase_order:<tenant_id>:<po_id>]` — tenant-qualified. |
| R13 | P3 | No scheduler-registration test | **CLOSED**: test asserts job id/04:45/Asia-Taipei/max_instances/coalesce/misfire without starting a scheduler. |

R1 (live Odoo sandbox validation) remains **OPEN-EXTERNAL** — still not executed; this remediation does not claim it.
This addendum records an **implementer** remediation run; it does not upgrade the independent reviewer verdict. Independent re-review required.
