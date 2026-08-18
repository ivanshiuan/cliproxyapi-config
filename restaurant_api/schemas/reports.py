"""Pydantic v2 schemas for the Reports router (營運報表).

Contract reference: ``docs/13_haodian_pos_comparison.md`` G8 — cloud
reports matching 好點 POS's feature set, but every report additionally
carries cost (COGS) and gross-margin columns, which is our wedge.

Read-only surface: there are no request bodies — every endpoint is a GET
with query params — so this module only defines response models.

Conventions (same as ``schemas/orders.py``):
- All money / qty fields are ``Decimal`` — never float.
- Models are ``frozen=True``; responses are built keyword-by-keyword in
  the service layer (no ``from_attributes`` needed for aggregates).
- Timestamps are rendered Asia/Taipei by the service layer.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ──────────────────────────────────────────────────────────────────────────
# Shared fragments
# ──────────────────────────────────────────────────────────────────────────


class PaymentMethodBreakdown(BaseModel):
    """Per-payment-method aggregate (cash / credit / linepay / …)."""

    model_config = ConfigDict(frozen=True)

    method: str
    amount: Decimal
    count: int


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/daily
# ──────────────────────────────────────────────────────────────────────────


class DailyReportResponse(BaseModel):
    """Single business-day summary — revenue AND cost side by side."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    business_date: date
    gross_revenue: Decimal
    order_count: int
    refund_count: int
    refund_amount: Decimal
    cogs_total: Decimal
    gross_margin: Decimal
    payments: list[PaymentMethodBreakdown] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/monthly
# ──────────────────────────────────────────────────────────────────────────


class MonthlyReportRow(BaseModel):
    """One business day within the month."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    revenue: Decimal
    orders: int
    cogs: Decimal
    margin: Decimal


class MonthlyReportResponse(BaseModel):
    """Day-by-day rows plus month totals."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    year: int
    month: int
    days: list[MonthlyReportRow] = Field(default_factory=list)
    total_revenue: Decimal
    total_orders: int
    total_cogs: Decimal
    total_margin: Decimal


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/products
# ──────────────────────────────────────────────────────────────────────────


class ProductReportRow(BaseModel):
    """Sales ranking row for one menu item — with margin, not just revenue."""

    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    name: str
    qty_sold: Decimal
    revenue: Decimal
    cogs: Decimal
    margin: Decimal


class ProductsReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    store_id: UUID
    date_from: date
    date_to: date
    items: list[ProductReportRow] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/period
# ──────────────────────────────────────────────────────────────────────────


class PeriodReportResponse(BaseModel):
    """Revenue for a daypart (e.g. lunch 11-14, Asia/Taipei clock).

    Hour window is half-open: ``hour_from <= local_hour < hour_to``.
    """

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    date_from: date
    date_to: date
    hour_from: int
    hour_to: int
    revenue: Decimal
    order_count: int
    avg_ticket: Decimal


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/reconciliation
# ──────────────────────────────────────────────────────────────────────────


class OpenOrderRow(BaseModel):
    """An order still open at report time — money not yet in the till."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    order_no: str
    amount: Decimal
    opened_at: datetime  # rendered Asia/Taipei by the service


class RefundRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_no: str
    amount: Decimal
    reason: str | None = None


class ReconciliationReportResponse(BaseModel):
    """End-of-day 對帳: open orders, refunds, tender totals, expected cash."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    business_date: date
    open_orders: list[OpenOrderRow] = Field(default_factory=list)
    refunds: list[RefundRow] = Field(default_factory=list)
    payments: list[PaymentMethodBreakdown] = Field(default_factory=list)
    cash_expected: Decimal


__all__ = [
    "DailyReportResponse",
    "MonthlyReportResponse",
    "MonthlyReportRow",
    "OpenOrderRow",
    "PaymentMethodBreakdown",
    "PeriodReportResponse",
    "ProductReportRow",
    "ProductsReportResponse",
    "ReconciliationReportResponse",
    "RefundRow",
]
