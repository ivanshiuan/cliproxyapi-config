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
    service_charge_total: Decimal = Decimal("0")
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


class HourlySlice(BaseModel):
    """One hour-of-day's slice of the range (Asia/Taipei wall clock)."""

    model_config = ConfigDict(frozen=True)

    hour: int  # 0-23, bucketed on order.closed_at converted to TPE
    order_count: int
    net_sales: Decimal


class HourlyReport(BaseModel):
    """時段分析 — which hour of the day actually makes money.

    Always 24 rows (hour 0..23), zero-filled for hours with no sales, so the
    caller can chart it directly without gap-filling client-side.
    """

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    date_from: date
    date_to: date
    slices: list[HourlySlice]


class ChannelSlice(BaseModel):
    """One order_type's (內用/外帶/外送) slice of the range."""

    model_config = ConfigDict(frozen=True)

    order_type: str
    order_count: int
    net_sales: Decimal
    # net_sales / total net_sales for the range, 0 when the range had no sales.
    share: Decimal


class ChannelReport(BaseModel):
    """通路分佈 — dine-in vs takeout vs delivery revenue share."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    date_from: date
    date_to: date
    net_sales: Decimal
    slices: list[ChannelSlice]


class StoreSlice(BaseModel):
    """One store's slice of the tenant-wide range (總部多店儀表板)."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    store_name: str
    order_count: int
    gross_sales: Decimal
    net_sales: Decimal
    avg_ticket: Decimal


class HqDashboard(BaseModel):
    """總部多店儀表板 (#42) — every active store under the tenant, ranked by
    ``net_sales`` descending, plus the tenant-wide total. Same money math as
    ``SalesReport`` per store, so a row here always matches what
    ``/reports/sales?store_id=<that store>`` returns for the same range."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    date_from: date
    date_to: date
    store_count: int
    total_order_count: int
    total_net_sales: Decimal
    stores: list[StoreSlice]


__all__ = [
    "ChannelReport",
    "ChannelSlice",
    "HourlyReport",
    "HourlySlice",
    "HqDashboard",
    "PaymentBreakdown",
    "SalesReport",
    "StoreSlice",
    "TopItem",
]
