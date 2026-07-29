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
2. Groups: `Accounting / Billing` only. NOT Settings, NOT Administration, NOT Purchase-manager.
3. ACL: mirror the matrix above (read on the six models; create/write only
   `res.partner` and `account.move`).
4. Record rules restricting the service user to: the single allowed company id;
   journals `PUR`/`SAL`/`MISC`; `move_type in ('in_invoice','entry')`.
5. Separate credentials per environment (local/staging/prod); prod keys only in the
   prod secret store; `.env` is gitignored (verified: `.env` untracked).
6. Status: **EXTERNAL_DEPENDENCY_BLOCKED** for live verification — checklist above
   must be executed and evidenced during first real deployment.
