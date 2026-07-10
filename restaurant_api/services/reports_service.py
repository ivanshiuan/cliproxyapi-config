"""營運報表 + 後台匯出 — read-only aggregations over closed orders.

Design:
- Scope is always (tenant, store, business_date range, status=CLOSED). Open /
  voided orders never count toward sales.
- Sales totals reuse ``orders_service._compute_net_revenue`` per order so the
  report's ``net_sales`` equals the sum of what the till actually charged — no
  second, drifting implementation of the discount stack.
- Per-order Python aggregation (not one big SQL SUM) is deliberate: the discount
  precedence lives in the calc engine, not in SQL. A day is hundreds of orders,
  so loading them is cheap; if this ever needs month-range scale, precompute
  into the ``mv_daily_pnl`` materialised view instead of reimplementing the
  stack in SQL.
- ``top_items`` *can* aggregate in SQL (line totals are pre-order-discount, a
  plain SUM), so it does — much cheaper than loading every line.
- CSV export is one row per closed order: what a bookkeeper reconciles against.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.errors import ValidationError
from ..models import MenuItem, Order, OrderLine, OrderStatus
from ..schemas.reports import PaymentBreakdown, SalesReport, TopItem
from . import orders_service

_TPE = ZoneInfo("Asia/Taipei")
_MONEY = Decimal("0.01")


def _validate_range(date_from: date, date_to: date | None) -> date:
    """Return the effective end date; a missing ``date_to`` means a single day."""
    end = date_to if date_to is not None else date_from
    if end < date_from:
        raise ValidationError(
            "date_to must not be before date_from",
            details={"date_from": date_from.isoformat(), "date_to": end.isoformat()},
        )
    return end


def _closed_orders_stmt(
    *, tenant_id: uuid.UUID, store_id: uuid.UUID, date_from: date, date_to: date
):
    return (
        select(Order)
        .where(
            Order.tenant_id == tenant_id,
            Order.store_id == store_id,
            Order.status == OrderStatus.CLOSED,
            Order.deleted_at.is_(None),
            Order.business_date >= date_from,
            Order.business_date <= date_to,
        )
        .options(
            selectinload(Order.lines),
            selectinload(Order.discounts),
            selectinload(Order.payments),
        )
        .order_by(Order.business_date, Order.closed_at)
    )


def _order_gross_net(order: Order) -> tuple[Decimal, Decimal, Decimal]:
    """Return (gross, net, item_qty) for one order."""
    gross = sum((ln.line_total for ln in order.lines), Decimal("0"))
    net = orders_service._compute_net_revenue(order)
    qty = sum((ln.qty for ln in order.lines), Decimal("0"))
    return gross, net, qty


async def sales_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    date_from: date,
    date_to: date | None = None,
) -> SalesReport:
    end = _validate_range(date_from, date_to)
    orders = (
        await session.execute(
            _closed_orders_stmt(
                tenant_id=tenant_id, store_id=store_id, date_from=date_from, date_to=end
            )
        )
    ).scalars().all()

    gross_total = Decimal("0")
    net_total = Decimal("0")
    item_total = Decimal("0")
    pay: dict[str, list] = {}  # method -> [count, amount]
    for o in orders:
        gross, net, qty = _order_gross_net(o)
        gross_total += gross
        net_total += net
        item_total += qty
        for p in o.payments:
            slot = pay.setdefault(p.method.value, [0, Decimal("0")])
            slot[0] += 1
            slot[1] += p.amount

    order_count = len(orders)
    avg_ticket = (
        (net_total / order_count).quantize(_MONEY) if order_count else Decimal("0.00")
    )
    payments = [
        PaymentBreakdown(method=m, count=c, amount=a)
        for m, (c, a) in sorted(pay.items())
    ]
    return SalesReport(
        store_id=store_id,
        date_from=date_from,
        date_to=end,
        order_count=order_count,
        item_count=item_total,
        gross_sales=gross_total,
        discount_total=gross_total - net_total,
        net_sales=net_total,
        avg_ticket=avg_ticket,
        payments=payments,
    )


async def top_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    date_from: date,
    date_to: date | None = None,
    limit: int = 20,
) -> list[TopItem]:
    end = _validate_range(date_from, date_to)
    rows = (
        await session.execute(
            select(
                OrderLine.menu_item_id,
                func.sum(OrderLine.qty).label("qty"),
                func.sum(OrderLine.line_total).label("revenue"),
            )
            .join(Order, OrderLine.order_id == Order.id)
            .where(
                Order.tenant_id == tenant_id,
                Order.store_id == store_id,
                Order.status == OrderStatus.CLOSED,
                Order.deleted_at.is_(None),
                Order.business_date >= date_from,
                Order.business_date <= end,
            )
            .group_by(OrderLine.menu_item_id)
            .order_by(func.sum(OrderLine.qty).desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return []

    ids = [r.menu_item_id for r in rows]
    names: dict[uuid.UUID, str] = {
        row.id: str(row.name)
        for row in (
            await session.execute(
                select(MenuItem.id, MenuItem.name).where(MenuItem.id.in_(ids))
            )
        ).all()
    }
    return [
        TopItem(
            menu_item_id=r.menu_item_id,
            name=names.get(r.menu_item_id),
            qty=r.qty,
            revenue=r.revenue,
        )
        for r in rows
    ]


async def orders_csv(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    date_from: date,
    date_to: date | None = None,
) -> str:
    """One row per closed order — the bookkeeper's reconciliation export."""
    end = _validate_range(date_from, date_to)
    orders = (
        await session.execute(
            _closed_orders_stmt(
                tenant_id=tenant_id, store_id=store_id, date_from=date_from, date_to=end
            )
        )
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "order_no",
            "business_date",
            "closed_at_tpe",
            "order_type",
            "channel",
            "item_qty",
            "gross",
            "discount",
            "net",
            "payments",
        ]
    )
    for o in orders:
        gross, net, qty = _order_gross_net(o)
        closed = o.closed_at.astimezone(_TPE).isoformat() if o.closed_at else ""
        payments = ";".join(
            f"{p.method.value}:{p.amount}" for p in sorted(o.payments, key=lambda x: x.method.value)
        )
        writer.writerow(
            [
                o.order_no,
                o.business_date.isoformat(),
                closed,
                o.order_type.value,
                o.channel.value,
                str(qty),
                str(gross),
                str(gross - net),
                str(net),
                payments,
            ]
        )
    return buf.getvalue()


__all__ = ["orders_csv", "sales_report", "top_items"]
