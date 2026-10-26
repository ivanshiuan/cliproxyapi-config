"""Response schema for ``GET /reports/daily-pnl`` -- the owner's "what's left in my pocket today".

All money is ``Decimal`` (never float); pydantic serialises it as a string in JSON so no
precision is lost on the wire. Amounts are NT$ quantised to 2 dp (ROUND_HALF_UP).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PnlLine(BaseModel):
    """One itemised line (a person's labor cost, a fixed-cost item, ...)."""

    model_config = ConfigDict(frozen=True)

    label: str
    amount: Decimal
    note: str | None = None


class DailyPnlReport(BaseModel):
    """One store, one business day."""

    model_config = ConfigDict(frozen=True)

    store_id: uuid.UUID
    store_name: str
    business_date: date

    # -- orders considered --
    order_count: int  # closed orders included in the numbers
    open_order_count: int  # not yet paid/closed: NOT in the numbers
    voided_order_count: int  # voided or refunded: NOT in the numbers

    # -- revenue --
    menu_gross: Decimal  # menu price x qty, before discounts (VAT-inclusive)
    discount_total: Decimal
    service_charge: Decimal  # service charge added at checkout
    billed_incl_vat: Decimal  # what customers owe: after discounts + service charge
    vat: Decimal  # business tax inside the billed amount, not the restaurant's revenue
    revenue_ex_vat: Decimal  # the P&L starting point

    # -- costs --
    cogs: Decimal  # ingredients behind the dishes actually sold
    cost_waste: Decimal
    cost_staff_meal: Decimal
    cost_tasting: Decimal
    payment_fees: Decimal  # card / pay-app fees (order_payments.fee_amount)
    gross_profit: Decimal  # revenue - cogs - waste - staff meal - tasting
    labor_cost: Decimal
    labor_lines: list[PnlLine]
    fixed_cost_total: Decimal
    fixed_cost_lines: list[PnlLine]
    net_profit: Decimal
    net_margin: Decimal | None  # net_profit / revenue_ex_vat as a ratio; None when revenue is 0

    # -- reconciliation: does the money collected match what the till said? --
    payments_total: Decimal
    payments_diff: Decimal  # payments_total - billed_incl_vat (0 = books and cash agree)

    assumptions: list[str]  # how the numbers were built: read these before trusting them
    warnings: list[str]  # things that make the profit look better (or worse) than reality
