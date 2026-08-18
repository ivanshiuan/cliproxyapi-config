"""營運報表 aggregation service — read-only, aggregates ORM tables directly.

Design notes
============
- Competes with 好點 POS cloud reports (see ``docs/13_haodian_pos_comparison.md``
  G8) but every report also carries COGS + gross margin — the wedge feature.
- Aggregates ``orders`` / ``order_lines`` / ``order_payments`` directly.
  Deliberately does NOT depend on the ``mv_daily_pnl`` materialised view so
  these reports are live (no refresh lag) and view-schema independent.
- Revenue = sum of ``order_lines.line_total`` for ``status='closed'`` orders
  only. Refunded / voided / open orders never count toward revenue.
- COGS = sum of ``order_lines.cogs_actual`` (NULL lines skipped — the BOM
  consumption job may not have priced them yet).
- ``business_date`` comparisons are direct (it's already the operational
  day). The /period daypart filter converts ``opened_at`` to Asia/Taipei
  wall-clock hours via ``timezone('Asia/Taipei', opened_at)``.
- Phase-1 tenancy: tenant is derived from the store row (same pattern as
  ``orders_service._resolve_tenant_from_store``), not trusted from headers.
"""

from __future__ import annotations

import calendar
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select

from ..api.errors import NotFoundError, ValidationError
from ..models.menu import MenuItem
from ..models.orders import Order, OrderLine, OrderPayment, OrderStatus, PaymentMethod
from ..models.stores import Store
from ..schemas.reports import (
    DailyReportResponse,
    MonthlyReportResponse,
    MonthlyReportRow,
    OpenOrderRow,
    PaymentMethodBreakdown,
    PeriodReportResponse,
    ProductReportRow,
    ProductsReportResponse,
    ReconciliationReportResponse,
    RefundRow,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("restaurant_api.services.reports")

_TPE = ZoneInfo("Asia/Taipei")
_ZERO = Decimal("0")
_CENT4 = Decimal("0.0001")


# ──────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────


async def _resolve_store_tenant(
    session: AsyncSession,
    store_id: uuid.UUID,
    request_tenant_id: uuid.UUID,
) -> uuid.UUID:
    """Phase-1: derive tenant from the store row; 404 if the store is unknown."""
    _ = request_tenant_id  # reserved for Phase 2 hard mismatch check
    stmt = select(Store.tenant_id).where(Store.id == store_id)
    tenant = (await session.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"store not found: {store_id}")
    return tenant


def _check_date_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ValidationError(
            "date_from must be <= date_to",
            details={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )


def _orders_scope(
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    status: OrderStatus | None = None,
) -> list:
    """Base WHERE clauses shared by every aggregate query."""
    clauses = [
        Order.tenant_id == tenant_id,
        Order.store_id == store_id,
        Order.deleted_at.is_(None),
    ]
    if status is not None:
        clauses.append(Order.status == status)
    return clauses


def _lines_revenue_stmt(clauses: list) -> Select:
    """SELECT sum(line_total), sum(cogs_actual) over lines of scoped orders."""
    return (
        select(
            func.coalesce(func.sum(OrderLine.line_total), 0),
            func.coalesce(func.sum(OrderLine.cogs_actual), 0),
        )
        .join(Order, OrderLine.order_id == Order.id)
        .where(*clauses)
    )


async def _payments_breakdown(
    session: AsyncSession,
    clauses: list,
) -> list[PaymentMethodBreakdown]:
    """Per-method tender totals over the scoped (closed) orders."""
    stmt = (
        select(
            OrderPayment.method,
            func.coalesce(func.sum(OrderPayment.amount), 0),
            func.count(OrderPayment.id),
        )
        .join(Order, OrderPayment.order_id == Order.id)
        .where(*clauses)
        .group_by(OrderPayment.method)
        .order_by(OrderPayment.method)
    )
    rows = (await session.execute(stmt)).all()
    return [
        PaymentMethodBreakdown(method=str(method), amount=Decimal(amount), count=int(count))
        for method, amount, count in rows
    ]


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/daily
# ──────────────────────────────────────────────────────────────────────────


async def daily_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    business_date: date,
) -> DailyReportResponse:
    tenant = await _resolve_store_tenant(session, store_id, tenant_id)

    closed = [*_orders_scope(tenant, store_id, OrderStatus.CLOSED), Order.business_date == business_date]
    refunded = [*_orders_scope(tenant, store_id, OrderStatus.REFUNDED), Order.business_date == business_date]

    revenue, cogs = (await session.execute(_lines_revenue_stmt(closed))).one()
    order_count = (
        await session.execute(select(func.count(Order.id)).where(*closed))
    ).scalar_one()
    refund_count = (
        await session.execute(select(func.count(Order.id)).where(*refunded))
    ).scalar_one()
    refund_amount, _ = (await session.execute(_lines_revenue_stmt(refunded))).one()
    payments = await _payments_breakdown(session, closed)

    revenue = Decimal(revenue)
    cogs = Decimal(cogs)
    logger.info("reports.daily store_id=%s business_date=%s", store_id, business_date)
    return DailyReportResponse(
        store_id=store_id,
        business_date=business_date,
        gross_revenue=revenue,
        order_count=int(order_count),
        refund_count=int(refund_count),
        refund_amount=Decimal(refund_amount),
        cogs_total=cogs,
        gross_margin=revenue - cogs,
        payments=payments,
    )


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/monthly
# ──────────────────────────────────────────────────────────────────────────


async def monthly_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    year: int,
    month: int,
) -> MonthlyReportResponse:
    if not 1 <= month <= 12:
        raise ValidationError("month must be between 1 and 12", details={"month": month})
    if not 2000 <= year <= 2100:
        raise ValidationError("year out of range", details={"year": year})
    tenant = await _resolve_store_tenant(session, store_id, tenant_id)

    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    closed = [
        *_orders_scope(tenant, store_id, OrderStatus.CLOSED),
        Order.business_date >= first,
        Order.business_date <= last,
    ]

    stmt = (
        select(
            Order.business_date,
            func.coalesce(func.sum(OrderLine.line_total), 0),
            func.count(func.distinct(Order.id)),
            func.coalesce(func.sum(OrderLine.cogs_actual), 0),
        )
        .join(OrderLine, OrderLine.order_id == Order.id)
        .where(*closed)
        .group_by(Order.business_date)
        .order_by(Order.business_date)
    )
    rows = (await session.execute(stmt)).all()

    days: list[MonthlyReportRow] = []
    total_revenue = _ZERO
    total_orders = 0
    total_cogs = _ZERO
    for bdate, revenue, orders, cogs in rows:
        revenue = Decimal(revenue)
        cogs = Decimal(cogs)
        days.append(
            MonthlyReportRow(
                business_date=bdate,
                revenue=revenue,
                orders=int(orders),
                cogs=cogs,
                margin=revenue - cogs,
            ),
        )
        total_revenue += revenue
        total_orders += int(orders)
        total_cogs += cogs

    logger.info("reports.monthly store_id=%s period=%04d-%02d", store_id, year, month)
    return MonthlyReportResponse(
        store_id=store_id,
        year=year,
        month=month,
        days=days,
        total_revenue=total_revenue,
        total_orders=total_orders,
        total_cogs=total_cogs,
        total_margin=total_revenue - total_cogs,
    )


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/products
# ──────────────────────────────────────────────────────────────────────────


async def products_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    date_from: date,
    date_to: date,
    limit: int = 20,
) -> ProductsReportResponse:
    _check_date_range(date_from, date_to)
    tenant = await _resolve_store_tenant(session, store_id, tenant_id)

    closed = [
        *_orders_scope(tenant, store_id, OrderStatus.CLOSED),
        Order.business_date >= date_from,
        Order.business_date <= date_to,
    ]
    revenue_sum = func.coalesce(func.sum(OrderLine.line_total), 0)
    stmt = (
        select(
            OrderLine.menu_item_id,
            MenuItem.name,
            func.coalesce(func.sum(OrderLine.qty), 0),
            revenue_sum,
            func.coalesce(func.sum(OrderLine.cogs_actual), 0),
        )
        .join(Order, OrderLine.order_id == Order.id)
        .join(MenuItem, OrderLine.menu_item_id == MenuItem.id)
        .where(*closed)
        .group_by(OrderLine.menu_item_id, MenuItem.name)
        .order_by(revenue_sum.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()

    items = [
        ProductReportRow(
            menu_item_id=menu_item_id,
            name=name,
            qty_sold=Decimal(qty),
            revenue=Decimal(revenue),
            cogs=Decimal(cogs),
            margin=Decimal(revenue) - Decimal(cogs),
        )
        for menu_item_id, name, qty, revenue, cogs in rows
    ]
    logger.info(
        "reports.products store_id=%s range=%s..%s rows=%d",
        store_id, date_from, date_to, len(items),
    )
    return ProductsReportResponse(
        store_id=store_id, date_from=date_from, date_to=date_to, items=items,
    )


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/period
# ──────────────────────────────────────────────────────────────────────────


async def period_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    date_from: date,
    date_to: date,
    hour_from: int,
    hour_to: int,
) -> PeriodReportResponse:
    _check_date_range(date_from, date_to)
    if not (0 <= hour_from < hour_to <= 24):
        raise ValidationError(
            "hour window must satisfy 0 <= hour_from < hour_to <= 24",
            details={"hour_from": hour_from, "hour_to": hour_to},
        )
    tenant = await _resolve_store_tenant(session, store_id, tenant_id)

    # Asia/Taipei wall-clock hour of opened_at (stored UTC in the DB).
    local_hour = func.extract("hour", func.timezone("Asia/Taipei", Order.opened_at))
    clauses = [
        *_orders_scope(tenant, store_id, OrderStatus.CLOSED),
        Order.business_date >= date_from,
        Order.business_date <= date_to,
        local_hour >= hour_from,
        local_hour < hour_to,
    ]

    revenue, _cogs = (await session.execute(_lines_revenue_stmt(clauses))).one()
    order_count = (
        await session.execute(select(func.count(Order.id)).where(*clauses))
    ).scalar_one()

    revenue = Decimal(revenue)
    count = int(order_count)
    avg_ticket = (revenue / count).quantize(_CENT4) if count else _ZERO
    logger.info(
        "reports.period store_id=%s range=%s..%s hours=%d-%d",
        store_id, date_from, date_to, hour_from, hour_to,
    )
    return PeriodReportResponse(
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        hour_from=hour_from,
        hour_to=hour_to,
        revenue=revenue,
        order_count=count,
        avg_ticket=avg_ticket,
    )


# ──────────────────────────────────────────────────────────────────────────
# GET /reports/reconciliation
# ──────────────────────────────────────────────────────────────────────────


async def reconciliation_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    business_date: date,
) -> ReconciliationReportResponse:
    tenant = await _resolve_store_tenant(session, store_id, tenant_id)
    day = Order.business_date == business_date

    # Open orders — money not yet collected. Sum via a LEFT JOIN so an
    # open order with no lines still shows up (amount 0).
    open_stmt = (
        select(
            Order.id,
            Order.order_no,
            func.coalesce(func.sum(OrderLine.line_total), 0),
            Order.opened_at,
        )
        .join(OrderLine, OrderLine.order_id == Order.id, isouter=True)
        .where(*_orders_scope(tenant, store_id, OrderStatus.OPEN), day)
        .group_by(Order.id, Order.order_no, Order.opened_at)
        .order_by(Order.opened_at)
    )
    open_orders = [
        OpenOrderRow(
            order_id=oid,
            order_no=order_no,
            amount=Decimal(amount),
            opened_at=opened_at.astimezone(_TPE),
        )
        for oid, order_no, amount, opened_at in (await session.execute(open_stmt)).all()
    ]

    # Refunds — order_no / amount / reason for the day's refunded orders.
    refund_stmt = (
        select(
            Order.order_no,
            func.coalesce(func.sum(OrderLine.line_total), 0),
            Order.refund_reason,
        )
        .join(OrderLine, OrderLine.order_id == Order.id, isouter=True)
        .where(*_orders_scope(tenant, store_id, OrderStatus.REFUNDED), day)
        .group_by(Order.id, Order.order_no, Order.refund_reason)
        .order_by(Order.order_no)
    )
    refunds = [
        RefundRow(order_no=order_no, amount=Decimal(amount), reason=reason)
        for order_no, amount, reason in (await session.execute(refund_stmt)).all()
    ]

    closed = [*_orders_scope(tenant, store_id, OrderStatus.CLOSED), day]
    payments = await _payments_breakdown(session, closed)
    cash_expected = next(
        (p.amount for p in payments if p.method == PaymentMethod.CASH.value),
        _ZERO,
    )

    logger.info(
        "reports.reconciliation store_id=%s business_date=%s open=%d refunds=%d",
        store_id, business_date, len(open_orders), len(refunds),
    )
    return ReconciliationReportResponse(
        store_id=store_id,
        business_date=business_date,
        open_orders=open_orders,
        refunds=refunds,
        payments=payments,
        cash_expected=cash_expected,
    )


__all__ = [
    "daily_report",
    "monthly_report",
    "period_report",
    "products_report",
    "reconciliation_report",
]
