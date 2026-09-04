# ODOO-THREAT-MODEL — Claude / restaurant_api / Odoo bridge

Assets: (1) the Odoo general ledger + AP (money truth), (2) the Odoo service
credential, (3) restaurant_api's operational data (inventory ledger, 食安 lot
traceability), (4) the integrity of the sync (no phantom/duplicate liabilities).

Trust boundaries: Claude/agent prompts (untrusted intent) → restaurant_api
domain code (trusted) → `enforce_operation_policy` chokepoint → Odoo external
API as a least-privilege service user → Odoo ACL/record rules (second wall).

| # | Threat | Vector | Mitigation | Status |
|---|--------|--------|------------|--------|
| T1 | Agent tricked into paying a vendor / registering payment | prompt injection, compromised upstream tool | `action_register_payment` in FORBIDDEN_METHODS; `account.payment` read-only; no payment tool exists on the surface | **Mitigated + tested** |
| T2 | Agent deletes/cancels posted accounting documents | same | `unlink`, `button_cancel`, `button_draft` always denied, before any network I/O | **Mitigated + tested** |
| T3 | Privilege escalation via Odoo settings/users models | arbitrary model access | model allow-list (6 models); `res.users`/`ir.config_parameter`/`res.groups`/`ir.model.access` raise `ModelNotAllowedError` pre-egress | **Mitigated + tested** |
| T4 | Phantom liability: unbalanced or fabricated vendor bill | bug or malicious entry | `JournalEntry.assert_balanced()` before every create; Decimal-only money (float rejected at construction); supplier must exist and not be soft-deleted or the PO is refused | **Mitigated + tested** |
| T5 | Duplicate drafts (double liability) after crash/retry | crash between Odoo-create and DB-commit; nightly re-run | two independent guards: `odoo_synced_at IS NULL` query fence + client-side find-before-create on `[src:<uuid7>]` marker; crash-replay test proves 1 move after forced re-run | **Mitigated + tested** |
| T6 | Service credential leak via logs/tracebacks | `repr(client)` in an exception report; debug logging | `api_key` is `field(repr=False)`; key only ever inside the JSON-RPC body which is never logged; leak-assertion tests scan repr/str/exception/caplog | **Mitigated + tested** |
| T7 | Booking into bank/cash journals (money movement) | crafted `journal_code` | `ALLOWED_JOURNAL_CODES = {PUR, SAL, MISC}` enforced in both stub and HTTP backends | **Mitigated + tested** |
| T8 | Silent infinite retry hammering Odoo / hiding failures | flaky network | bounded retry (transient-only, exponential backoff), per-PO attempt counter, dead-letter at 5 with `odoo_last_sync_error`, surfaced by `reconcile_sync_status` | **Mitigated + tested** |
| T9 | Odoo-side over-privilege (if client code is bypassed entirely) | attacker with the API key | Layer 2: least-privilege service user + ACL + record rules (company/journal/move_type) — prescriptive, `ODOO-PERMISSION-MATRIX.md` §Layer 2 | **EXTERNAL_DEPENDENCY_BLOCKED** (no live Odoo) |
| T10 | Auto-posting to the ledger without human review | config flip | default `ODOO_ALLOW_AUTO_POST=false`; posting additionally requires `post=True` per call; nightly sync never passes it | **Mitigated + tested** |
| T11 | Cross-tenant data mixing in the sync | multi-tenant DB | sync query and reconcile are tenant-scoped; PO/supplier both carry `tenant_id` (FK RESTRICT) | **Mitigated + tested (tenant-scoped fixtures)** |

Residual risks → `RISK-REGISTER.md`.
