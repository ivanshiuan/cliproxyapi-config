# STAGING-ACCOUNTING-CONFIG

---

## Critical Issue: AP Account Mapping

**Problem identified during sandbox run `sbx20260730090315` and
`sbx20260730100528`:**

When Odoo generates the payable counterpart line from `invoice_line_ids`,
it looks up `property_account_payable_id` on `res.partner` for the
partner, falling back to the company's default if not set on the partner.
In the sandbox, this resolved to `211000 Account Payable` (Odoo's default
chart entry).

In a real Taiwan accounting deployment, the AP account is typically:

- `2100` — 應付帳款 (Accounts Payable) in the standard Taiwan chart
- Or a custom code per the restaurant's accounting firm

**If `property_account_payable_id` is not explicitly configured on the
staging company and supplier partners, every vendor bill will post to
Odoo's default chart account (211000), which may not match the intended
Taiwan chart. This will cause reconciliation failures at production.**

---

## Required Pre-Staging Configuration Steps

### Step 1: Install Taiwan Chart of Accounts

In Odoo 17, the localization module for Taiwan is `l10n_tw` (if available)
or configure manually:

```
Odoo Settings -> Accounting -> Chart of Accounts
```

The minimum accounts required for `restaurant_api` integration:

| Account Code | Name | Type | Required For |
|---|---|---|---|
| 1310 | 存貨 (Inventory) | Asset (Current) | Invoice debit lines |
| 1360 | 進項稅額 (Input VAT) | Asset (Current) | VAT invoice lines (EXPLICIT_MODE) |
| 2100 | 應付帳款 (Accounts Payable) | Liability (Current) | Auto-generated payable |

### Step 2: Set Company Default AP Account

```
Accounting -> Configuration -> Settings -> Default Accounts
  -> Default Payable Account: 2100 應付帳款
```

### Step 3: Set Partner-Level AP Account (optional but recommended)

For each supplier partner created by `restaurant_api`, set:
```
res.partner.property_account_payable_id = account.account(code='2100')
```

This can be done via admin RPC after `svc-restaurant-api-staging` creates
the partner:
```python
# Admin-only provisioning script (not part of restaurant_api runtime)
partner_ids = client.search("res.partner", [["ref", "=", supplier_code]])
ap_account = client.search("account.account", [["code", "=", "2100"]])
client.write("res.partner", partner_ids, {"property_account_payable_id": ap_account[0]})
```

### Step 4: Create Purchase Journal

The sandbox used `Sandbox Purchases` journal with internal type `purchase`.
Staging must have:

```
Journal: Purchases (or 採購)
Code: PUR
Type: Purchase
Default account: 1310 存貨
```

The `restaurant_api` client looks up the journal by code `PUR` via
`_resolve_journal_id()`.

### Step 5: Verify Journal Fence

`client.py` allows only `PUR`, `SAL`, `MISC` journal codes
(`ALLOWED_JOURNAL_CODES`). The staging journal MUST have code exactly
`PUR`. If the chart requires a different code, `ALLOWED_JOURNAL_CODES`
must be updated before staging acceptance.

---

## EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE

The current implementation books Taiwan 5% input VAT as a manual invoice
line on account `1360`:

```python
# Example: PO total 1,260 TWD = 1,200 subtotal + 60 VAT
invoice_line_ids = [
    (0, 0, {"account_id": 1310_id, "name": "存貨", "quantity": 1.0, "price_unit": 1200.0}),
    (0, 0, {"account_id": 1360_id, "name": "進項稅額", "quantity": 1.0, "price_unit": 60.0}),
]
# Odoo generates: payable credit = 1,260 (sum of all invoice lines)
```

This is deliberately NOT a full Taiwan tax configuration. There are no Odoo
`account.tax` objects linked. The VAT amount is passed explicitly from
`PurchaseOrder.tax_amount`.

**Staging must NOT configure Odoo tax rules that auto-apply to these
accounts**, or the VAT will be double-counted.

---

## Staging Verification: AP Account Round-Trip

After the first vendor bill is created in staging:

1. Read back the move: `account.move.read([move_id], ["line_ids"])`
2. Find lines with `display_type == "payment_term"` (auto-generated
   payable)
3. Verify `account_id[1]` contains the code `2100` (not `211000` Odoo
   default)
4. Verify `credit == PurchaseOrder.total`

If the account code is `211000`, the chart of accounts is not properly
localized. Do not proceed to production with `211000`.
