"""Pydantic v2 schemas for the ``/reports`` router (營運報表 + 後台匯出).

Read-only aggregations over *closed* orders, scoped to a store and a
``business_date`` range. Money stays ``Decimal`` end-to-end — the totals here are
computed with the same discount engine the till uses at checkout, so a day's
report reconciles exactly with what the drawer took.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PaymentBreakdown(BaseModel):
    """One payment method's slice of the day: how many payments, how much."""

    model_config = ConfigDict(frozen=True)

    method: str
    count: int
    amount: Decimal


class SalesReport(BaseModel):
    """Day-close / range summary for one store.

    ``gross_sales`` is the sum of line totals before order-level discounts;
    ``net_sales`` is after the discount stack (percent → employee → amount →
    allowance → comp). ``discount_total = gross_sales - net_sales``. Payments are
    what actually settled the closed orders, split by method.
    """

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    date_from: date
    date_to: date
    order_count: int
    item_count: Decimal
    gross_sales: Decimal
    discount_total: Decimal
    net_sales: Decimal
    avg_ticket: Decimal
    payments: list[PaymentBreakdown]


class TopItem(BaseModel):
    """One row of the best-sellers ranking over the range."""

    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    name: str | None
    qty: Decimal
    revenue: Decimal


__all__ = ["PaymentBreakdown", "SalesReport", "TopItem"]
