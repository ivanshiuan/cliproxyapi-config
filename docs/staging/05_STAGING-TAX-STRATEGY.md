# STAGING-TAX-STRATEGY

---

## Phase 1 (Current): EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE

**What it is:**

The `restaurant_api` integration passes VAT as an explicit invoice line on
account `1360` with `price_unit = PurchaseOrder.tax_amount`. No Odoo
`account.tax` object is linked. Odoo treats this as a regular debit
invoice line and includes it in the payable total.

**Why it was chosen:**

- Avoids the complexity of configuring Taiwan tax codes, HST/GST/QHST
  mappings, and Odoo's tax reconciliation engine
- The tax amount is already computed upstream (from the actual supplier
  invoice)
- Verified working on real Odoo 17 (sandbox run `sbx20260730100528`)
- Appropriate for Phase 1 where the goal is AP integration, not tax
  reporting

**Limitations:**

- Odoo's tax report / VAT report will show `0 TWD` for tax from this
  integration
- No automatic tax reconciliation or tax closing entries
- Does not integrate with 統一發票 (uniform invoice) lifecycle in Odoo
- Not compliant with full electronic accounting submission requirements if
  those are activated in Odoo

**What must NOT happen in staging:**

- Do NOT configure Odoo `account.tax` objects that auto-apply to accounts
  `1310` or `1360`
- Do NOT enable Odoo's automatic tax rounding
- Do NOT configure fiscal positions that remap these accounts

---

## Phase 2 (Future, NOT this task): Full Taiwan Tax Integration

Phase 2 would require:

1. **Configure `account.tax` for Taiwan 5% input VAT:**
   ```
   Name: 進項稅額 5%
   Type: Purchase
   Tax Account: 1360
   Amount: 5%
   ```

2. **Change wire payload:** Replace the manual 1360 invoice line with:
   ```python
   {"tax_ids": [(4, taiwan_vat_5pct_tax_id)]}
   ```
   on the 1310 inventory line. Odoo auto-computes and books the 1360 tax
   line.

3. **Update `_build_invoice_vals`** in `client.py` to pass `tax_ids`
   instead of explicit VAT line.

4. **Remove EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE label** from
   code/docs.

5. **Full test cycle** including FakeOdooServer update to model tax
   distribution.

**Phase 2 is out of scope for this staging deployment.** A separate PR is
required.

---

## Tax Staging Acceptance Criteria

For Phase 1 staging acceptance:

- Vendor bill `amount_total` == `PurchaseOrder.total` (subtotal +
  tax_amount)
- Payable credit == `PurchaseOrder.total`
- Account 1310 debit == `PurchaseOrder.subtotal`
- Account 1360 debit == `PurchaseOrder.tax_amount`
- No Odoo tax objects linked to the move
- Ivan acknowledges: Odoo tax reports will show zero for this integration
  until Phase 2
