"""Odoo back-office integration.

restaurant_api stays the source of truth for operations (inventory ledger,
food-safety traceability, labour, POS, loyalty). Odoo owns the back office:
the general ledger, accounts payable (廠商帳), the supplier master and the
formal procurement documents. This package is the one-way-mostly bridge that
pushes *summary* financial entries into Odoo and reads payment status back.

Public surface mirrors ``integrations/line``: a client contract with a stub
and an HTTP backend (``client``), plus the pure event -> journal-entry
translation (``postings``).
"""

from __future__ import annotations

from .client import (
    ALLOWED_CURRENCIES,
    ALLOWED_JOURNAL_CODES,
    ALLOWED_MODELS,
    ALLOWED_WRITE_OPERATIONS,
    FORBIDDEN_METHODS,
    READ_METHODS,
    HttpOdooClient,
    InvalidPartnerError,
    ModelNotAllowedError,
    OdooApiError,
    OdooClient,
    OdooTransientError,
    OperationNotAllowedError,
    PostingNotPermittedError,
    StubOdooClient,
    SupplierRecord,
    UnsupportedCurrencyError,
    enforce_operation_policy,
    get_odoo,
    odoo_run_id,
    reset_odoo,
)
from .postings import (
    DEFAULT_CHART,
    AccountChart,
    DailySales,
    JournalEntry,
    JournalLine,
    PayrollAccrual,
    PurchaseBill,
    UnbalancedEntryError,
    WasteLoss,
    daily_sales_journal,
    payroll_journal,
    purchase_to_vendor_bill,
    waste_loss_journal,
)

__all__ = [
    "ALLOWED_CURRENCIES",
    "ALLOWED_JOURNAL_CODES",
    "ALLOWED_MODELS",
    "ALLOWED_WRITE_OPERATIONS",
    "DEFAULT_CHART",
    "FORBIDDEN_METHODS",
    "READ_METHODS",
    "AccountChart",
    "DailySales",
    "HttpOdooClient",
    "InvalidPartnerError",
    "JournalEntry",
    "JournalLine",
    "ModelNotAllowedError",
    "OdooApiError",
    "OdooClient",
    "OdooTransientError",
    "OperationNotAllowedError",
    "PayrollAccrual",
    "PostingNotPermittedError",
    "PurchaseBill",
    "StubOdooClient",
    "SupplierRecord",
    "UnbalancedEntryError",
    "UnsupportedCurrencyError",
    "WasteLoss",
    "daily_sales_journal",
    "enforce_operation_policy",
    "get_odoo",
    "odoo_run_id",
    "payroll_journal",
    "purchase_to_vendor_bill",
    "reset_odoo",
    "waste_loss_journal",
]
