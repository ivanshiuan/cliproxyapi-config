# ODOO-TOOL-CONTRACT — the only surface an agent may call

Principle: **capability boundary = permission boundary.** No generic
`execute_kw`, no arbitrary model/method, no `unlink`, no `sudo`, no SQL is
exported anywhere. `_execute_kw` is a private transport detail and every call
through it passes `enforce_operation_policy` first, so even in-process misuse
cannot exceed the matrix.

## High-level domain surface (public)

| Tool | Maps to | Risk tier | Autonomous? |
|---|---|---|---|
| `OdooClient.get_ap_aging(supplier_ref?)` | read outstanding vendor bills | read | ✅ |
| `OdooClient.find_move(external_id)` | read, dedup lookup by `[src:…]` marker | read | ✅ |
| `OdooClient.upsert_supplier(SupplierRecord)` | `res.partner` create/write (supplier_rank=1) | low write | ✅ |
| `OdooClient.create_vendor_bill(entry)` | `account.move` create, `move_type=in_invoice`, **draft** | low write | ✅ |
| `OdooClient.post_journal_entry(entry)` | `account.move` create, `move_type=entry`, **draft** | low write | ✅ |
| `…(entry, post=True)` | + `action_post` | HIGH | ❌ unless `ODOO_ALLOW_AUTO_POST` (one-time policy, default false) |
| `sync_purchase_orders(...)` (service) | receipt → supplier upsert + draft bill + stamp | low write | ✅ nightly job 04:45 |
| `reconcile_sync_status(...)` (service) | pending/synced/dead-letter counts | read | ✅ |

Mission-named equivalents: `get_supplier`≈partner search inside upsert;
`create_supplier_draft`≈`upsert_supplier`; `create_vendor_bill_draft`≈
`create_vendor_bill` (draft is the default and the only autonomous mode);
`sync_purchase_receipt`≈`sync_purchase_orders`; `get_payables_summary`≈
`get_ap_aging`; `reconcile_sync_status`≈same name. `create_purchase_order_draft`
is intentionally **not** an Odoo tool — purchase orders live in restaurant_api
(`docs/20` SSOT split); Odoo receives only their financial shadow.

## Input contracts

- All money enters as `Decimal`; `float` raises `TypeError` at dataclass
  construction (`postings._reject_float`).
- Every `JournalEntry` must satisfy `assert_balanced()` — the transport layer
  calls it again before any wire I/O; an unbalanced entry cannot leave the process.
- `entry.external_id` is the idempotency key (restaurant_api uuid7), embedded
  as `[src:<id>]` in the move ref and used for find-before-create.
- `journal_code` must be in `{PUR, SAL, MISC}`.

## Guarantees to callers

1. A denied operation raises (`ModelNotAllowedError` / `OperationNotAllowedError`
   / `PostingNotPermittedError`) **before** any network request (proved by tests
   whose mock transport raises on contact).
2. Same `external_id` twice ⇒ same Odoo move, never a duplicate (stub + HTTP,
   including crash-replay).
3. Transient failures retry with bounded exponential backoff; application errors
   never retry.
4. The API key never appears in `repr`, `str`, exceptions, or logs.
