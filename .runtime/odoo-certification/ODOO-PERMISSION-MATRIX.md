# ODOO-PERMISSION-MATRIX — what the integration can and cannot do

Two enforcement layers. **Layer 1 (client-side, code, tested here)** is
`enforce_operation_policy()` in `restaurant_api/integrations/odoo/client.py` —
the single chokepoint every call passes; nothing else can emit a request.
**Layer 2 (Odoo-side, prescriptive)** is the service account's ACL/record
rules — cannot be live-verified in this environment (no Odoo instance:
EXTERNAL_DEPENDENCY_BLOCKED) and MUST be applied at deployment.

## Layer 1 — client-side CRUD matrix (enforced + adversarially tested)

| Model | read* | create | write | action_post | unlink | payment ops |
|---|---|---|---|---|---|---|
| `res.partner` | ✅ | ✅ | ✅ | — | ❌ always | — |
| `account.move` | ✅ | ✅ (draft) | ❌ | ⚠️ only if `ODOO_ALLOW_AUTO_POST` | ❌ always | ❌ always |
| `account.move.line` | ✅ | ❌ | ❌ | — | ❌ | — |
| `account.account` | ✅ | ❌ | ❌ | — | ❌ | — |
| `account.journal` | ✅ | ❌ | ❌ | — | ❌ | — |
| `account.payment` | ✅ **read-only** | ❌ | ❌ | — | ❌ | ❌ `action_register_payment` in FORBIDDEN_METHODS |
| any other model (`res.users`, `ir.config_parameter`, `res.groups`, `ir.model.access`, …) | ❌ `ModelNotAllowedError` before any request | ❌ | ❌ | ❌ | ❌ | ❌ |

\* read = `read/search/search_read/search_count/fields_get/name_get` only.

Hard denials (`FORBIDDEN_METHODS`, checked FIRST, not configurable):
`unlink`, `button_cancel`, `button_draft`, `action_register_payment`,
`action_archive`, `toggle_active`, `sudo`.

Journal fence: moves may only target journals `PUR` / `SAL` / `MISC`
(`ALLOWED_JOURNAL_CODES`); bank/cash journals are structurally unreachable —
money movement is not this bridge's job.

Autonomous action policy (mission mapping):
- reads → allowed
- draft creation (vendor bill / journal entry / supplier upsert) → allowed
- reconciliation (`reconcile_sync_status`) → allowed
- retry failed sync → allowed, bounded (dead-letter at 5 attempts)
- confirm purchase order in Odoo → not exposed at all (PO lives in restaurant_api)
- post vendor bill → denied unless `ODOO_ALLOW_AUTO_POST=true` (default false)
- register payment → **always denied** (FORBIDDEN_METHODS)
- delete/cancel posted document → **always denied** (unlink/button_cancel forbidden)
- user/security/settings changes → **always denied** (models outside allow-list)

Tests attacking the boundary (all pass; the mock transport RAISES if any denied
call produces network traffic): `test_policy_blocks_unlink_even_on_allowed_model`,
`test_policy_blocks_payment_registration`, `test_policy_blocks_arbitrary_model_and_settings`,
`test_policy_blocks_unlisted_write_method`, `test_policy_blocks_disallowed_journal`,
`test_e2e_forbidden_operations_never_reach_the_server`.

## Layer 2 — Odoo-side deployment prescription (apply when Odoo exists)

1. Dedicated service user (e.g. `svc-restaurant-api`), **not** admin, login disabled
   for web UI where the edition allows; authenticate with an **API key**, never a password.
2. Groups — the **sandbox-confirmed minimal set** the integration actually needs
   (live run `sbx20260730090315`, Odoo 17.0):
   - **Accounting** (`account.group_account_user`) — read journals/accounts,
     create draft vendor bills and journal entries.
   - **Contact Creation** (`base.group_partner_manager`) — upsert the supplier
     `res.partner`. Without it, `res.partner.create` is refused by Odoo
     ("You are not allowed to create 'Contact' records"). Odoo also grants this
     via **Purchase / Administrator**; this bridge uses Contact Creation and does
     **not** require the Purchase app (it never touches `purchase.order` in Odoo).
   These groups grant **no** access to Settings, Administration, or User
   management. Explicitly do **NOT** assign: Settings/Administration,
   User management, Payment creation, Bank/Cash operations, or rights to
   delete/cancel posted documents.
3. ACL: mirror the matrix above (read on the six models; create/write only
   `res.partner` and `account.move`).
4. Record rules restricting the service user to: the single allowed company id;
   journals `PUR`/`SAL`/`MISC`; `move_type in ('in_invoice','entry')`. The
   move-type fence is now **also enforced client-side** in
   `_validate_move_inputs` (`SUPPORTED_MOVE_TYPES`) — any other type is refused
   before egress.
5. Separate credentials per environment (local/staging/prod); prod keys only in the
   prod secret store; `.env` is gitignored (verified: `.env` untracked).
6. Live-sandbox status: a localhost Odoo 17 sandbox has been provisioned and the
   Layer-2 model exercised end to end (non-admin API-key auth, forbidden-op zero
   egress, negatives). Production Odoo remains out of scope for this task.

## Odoo 17 vendor-bill wiring (compatibility note)

Vendor bills (`move_type='in_invoice'`) are sent with **`invoice_line_ids`**
(one line per debit business line: inventory + input VAT), and Odoo generates
the accounts-payable counterpart from `partner_id` + the line total. Raw
`debit`/`credit` `line_ids` are **not** used for invoices — Odoo 17's invoice
engine recomputes them and rejects the move as unbalanced (confirmed live).
General entries (`move_type='entry'`) keep raw `line_ids`. Input VAT is booked
as an explicit invoice line on account `1360`
(`EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE`); no Odoo tax object is configured
in this mode — that is deliberately **not** a full Taiwan tax configuration.
   must be executed and evidenced during first real deployment.
