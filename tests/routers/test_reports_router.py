"""Router-level integration tests for ``/reports`` (營運報表, G8).

Coverage map (task acceptance criteria):
    test_daily_gross_revenue_and_order_count   → daily revenue over closed orders
    test_daily_excludes_refunded_from_revenue  → refunds excluded + counted separately
    test_daily_payments_breakdown              → per-method amount/count
    test_daily_cogs_null_skipped_margin        → NULL cogs skipped; margin = rev - cogs
    test_daily_unknown_store_404               → store scoping / 404
    test_monthly_rows_and_totals               → per-day rows + month totals
    test_monthly_month_13_422                  → month validation
    test_products_ranking_by_revenue_with_margin → ranking desc + margin columns
    test_products_limit_param                  → limit query param
    test_period_tpe_hour_boundary              → Asia/Taipei hour filter boundary
    test_period_no_orders_zero_avg_ticket      → zero-division guard
    test_period_date_from_after_date_to_422    → date range validation
    test_reconciliation_open_refund_cash       → open orders, refunds, cash_expected

All rows are scoped to the per-test ``seed_tenant`` / ``seed_store`` and
rolled back by the savepoint fixture — no full-table assertions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import MenuItem
from restaurant_api.models.orders import (
    Order,
    OrderLine,
    OrderPayment,
    OrderStatus,
    PaymentMethod,
)
from restaurant_api.routers import reports as reports_router_module

# Mount /reports onto the shared app (main.py doesn't include it —
# file-ownership boundary). ``_mount`` is idempotent.
reports_router_module._mount()

BDATE = date(2026, 8, 10)


# ──────────────────────────────────────────────────────────────────────────
# Seed helper — one order with lines/payments, scoped to the seed tenant
# ──────────────────────────────────────────────────────────────────────────


async def _mk_order(
    db_session: AsyncSession,
    seed_tenant: Any,
    seed_store: Any,
    *,
    status: OrderStatus = OrderStatus.CLOSED,
    business_date: date = BDATE,
    opened_at: datetime | None = None,
    lines: list[tuple[uuid.UUID, str, str, str | None]] = (),  # type: ignore[assignment]
    payments: list[tuple[PaymentMethod, str]] = (),  # type: ignore[assignment]
    refund_reason: str | None = None,
) -> Order:
    """Create an order + lines + payments. Line tuple: (menu_item_id, qty,
    unit_price, cogs_actual-or-None); payment tuple: (method, amount)."""
    order = Order(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        order_no=f"RPT-{uuid.uuid4().hex[:8]}",
        business_date=business_date,
        status=status,
        refund_reason=refund_reason,
    )
    if opened_at is not None:
        order.opened_at = opened_at
    db_session.add(order)
    await db_session.flush()

    for menu_item_id, qty, unit_price, cogs in lines:
        q, p = Decimal(qty), Decimal(unit_price)
        db_session.add(
            OrderLine(
                tenant_id=seed_tenant.id,
                order_id=order.id,
                menu_item_id=menu_item_id,
                qty=q,
                unit_price=p,
                line_total=q * p,
                cogs_actual=Decimal(cogs) if cogs is not None else None,
            ),
        )
    for method, amount in payments:
        db_session.add(
            OrderPayment(
                tenant_id=seed_tenant.id,
                order_id=order.id,
                method=method,
                amount=Decimal(amount),
            ),
        )
    await db_session.flush()
    return order


def _err(body: dict[str, Any]) -> dict[str, Any]:
    """Inner error dict regardless of envelope shape."""
    if "error" in body:
        return body["error"]
    if "detail" in body and isinstance(body["detail"], dict):
        return body["detail"]
    return body


# ──────────────────────────────────────────────────────────────────────────
# /reports/daily
# ──────────────────────────────────────────────────────────────────────────


async def test_daily_gross_revenue_and_order_count(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Two closed orders → gross_revenue is the sum of their line_totals."""
    mi = seed_menu_item.id
    await _mk_order(db_session, seed_tenant, seed_store, lines=[(mi, "2", "250.00", None)])
    await _mk_order(db_session, seed_tenant, seed_store, lines=[(mi, "1", "100.00", None)])

    resp = await client.get(
        "/reports/daily",
        params={"store_id": str(seed_store.id), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["gross_revenue"]) == Decimal("600.00")
    assert body["order_count"] == 2


async def test_daily_excludes_refunded_from_revenue(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Refunded orders never count toward revenue; they show up as refunds."""
    mi = seed_menu_item.id
    await _mk_order(db_session, seed_tenant, seed_store, lines=[(mi, "1", "300.00", None)])
    await _mk_order(
        db_session, seed_tenant, seed_store,
        status=OrderStatus.REFUNDED,
        lines=[(mi, "1", "500.00", None)],
        refund_reason="客訴",
    )

    resp = await client.get(
        "/reports/daily",
        params={"store_id": str(seed_store.id), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["gross_revenue"]) == Decimal("300.00")
    assert body["refund_count"] == 1
    assert Decimal(body["refund_amount"]) == Decimal("500.00")


async def test_daily_payments_breakdown(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Per-method breakdown: cash x2 orders, credit x1."""
    mi = seed_menu_item.id
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[(mi, "1", "200.00", None)],
        payments=[(PaymentMethod.CASH, "200.00")],
    )
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[(mi, "1", "150.00", None)],
        payments=[(PaymentMethod.CASH, "50.00"), (PaymentMethod.CREDIT, "100.00")],
    )

    resp = await client.get(
        "/reports/daily",
        params={"store_id": str(seed_store.id), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 200
    breakdown = {p["method"]: p for p in resp.json()["payments"]}
    assert Decimal(breakdown["cash"]["amount"]) == Decimal("250.00")
    assert breakdown["cash"]["count"] == 2
    assert Decimal(breakdown["credit"]["amount"]) == Decimal("100.00")
    assert breakdown["credit"]["count"] == 1


async def test_daily_cogs_null_skipped_margin(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """A line with NULL cogs_actual is skipped; margin = revenue - cogs."""
    mi = seed_menu_item.id
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[(mi, "1", "250.00", "80.00"), (mi, "1", "100.00", None)],
    )

    resp = await client.get(
        "/reports/daily",
        params={"store_id": str(seed_store.id), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["cogs_total"]) == Decimal("80.00")
    assert Decimal(body["gross_margin"]) == Decimal("270.00")  # 350 - 80


async def test_daily_unknown_store_404(client):
    """A store_id that doesn't exist → 404 NOT_FOUND envelope."""
    resp = await client.get(
        "/reports/daily",
        params={"store_id": str(uuid.uuid4()), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 404
    assert _err(resp.json())["code"] == "NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────────
# /reports/monthly
# ──────────────────────────────────────────────────────────────────────────


async def test_monthly_rows_and_totals(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Orders on two days → two rows + correct month totals."""
    mi = seed_menu_item.id
    await _mk_order(
        db_session, seed_tenant, seed_store,
        business_date=date(2026, 8, 10),
        lines=[(mi, "1", "300.00", "90.00")],
    )
    await _mk_order(
        db_session, seed_tenant, seed_store,
        business_date=date(2026, 8, 11),
        lines=[(mi, "2", "100.00", "60.00")],
    )

    resp = await client.get(
        "/reports/monthly",
        params={"store_id": str(seed_store.id), "year": 2026, "month": 8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["days"]) == 2
    assert body["days"][0]["business_date"] == "2026-08-10"
    assert Decimal(body["days"][0]["revenue"]) == Decimal("300.00")
    assert Decimal(body["total_revenue"]) == Decimal("500.00")
    assert body["total_orders"] == 2
    assert Decimal(body["total_cogs"]) == Decimal("150.00")
    assert Decimal(body["total_margin"]) == Decimal("350.00")


async def test_monthly_month_13_422(client, seed_store):
    """month=13 → 422 VALIDATION_ERROR."""
    resp = await client.get(
        "/reports/monthly",
        params={"store_id": str(seed_store.id), "year": 2026, "month": 13},
    )
    assert resp.status_code == 422
    assert _err(resp.json())["code"] == "VALIDATION_ERROR"


# ──────────────────────────────────────────────────────────────────────────
# /reports/products
# ──────────────────────────────────────────────────────────────────────────


async def _second_menu_item(db_session, seed_tenant, seed_store) -> MenuItem:
    item = MenuItem(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Test Fries",
        price=Decimal("80.00"),
        is_available=True,
        allergens=[],
    )
    db_session.add(item)
    await db_session.flush()
    return item


async def test_products_ranking_by_revenue_with_margin(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Burger out-earns fries → ranked first; margin = revenue - cogs."""
    fries = await _second_menu_item(db_session, seed_tenant, seed_store)
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[
            (seed_menu_item.id, "2", "250.00", "160.00"),  # burger 500 rev
            (fries.id, "3", "80.00", "60.00"),  # fries 240 rev
        ],
    )

    resp = await client.get(
        "/reports/products",
        params={
            "store_id": str(seed_store.id),
            "date_from": BDATE.isoformat(),
            "date_to": BDATE.isoformat(),
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [row["name"] for row in items] == ["Test Burger", "Test Fries"]
    top = items[0]
    assert top["menu_item_id"] == str(seed_menu_item.id)
    assert Decimal(top["qty_sold"]) == Decimal("2")
    assert Decimal(top["revenue"]) == Decimal("500.00")
    assert Decimal(top["cogs"]) == Decimal("160.00")
    assert Decimal(top["margin"]) == Decimal("340.00")


async def test_products_limit_param(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """limit=1 truncates the ranking to the top seller only."""
    fries = await _second_menu_item(db_session, seed_tenant, seed_store)
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[(seed_menu_item.id, "1", "250.00", None), (fries.id, "1", "80.00", None)],
    )

    resp = await client.get(
        "/reports/products",
        params={
            "store_id": str(seed_store.id),
            "date_from": BDATE.isoformat(),
            "date_to": BDATE.isoformat(),
            "limit": 1,
        },
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Test Burger"


# ──────────────────────────────────────────────────────────────────────────
# /reports/period
# ──────────────────────────────────────────────────────────────────────────


async def test_period_tpe_hour_boundary(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Lunch window 11-14 TPE: 03:30 UTC (11:30 TPE) is in; 06:30 UTC
    (14:30 TPE) is out — the filter must apply Asia/Taipei, not UTC hours."""
    mi = seed_menu_item.id
    await _mk_order(
        db_session, seed_tenant, seed_store,
        opened_at=datetime(2026, 8, 10, 3, 30, tzinfo=UTC),  # 11:30 TPE — in
        lines=[(mi, "1", "300.00", None)],
    )
    await _mk_order(
        db_session, seed_tenant, seed_store,
        opened_at=datetime(2026, 8, 10, 6, 30, tzinfo=UTC),  # 14:30 TPE — out
        lines=[(mi, "1", "999.00", None)],
    )

    resp = await client.get(
        "/reports/period",
        params={
            "store_id": str(seed_store.id),
            "date_from": BDATE.isoformat(),
            "date_to": BDATE.isoformat(),
            "hour_from": 11,
            "hour_to": 14,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_count"] == 1
    assert Decimal(body["revenue"]) == Decimal("300.00")
    assert Decimal(body["avg_ticket"]) == Decimal("300.00")


async def test_period_no_orders_zero_avg_ticket(client, seed_store):
    """Empty window → revenue 0, order_count 0, avg_ticket 0 (no div-by-zero)."""
    resp = await client.get(
        "/reports/period",
        params={
            "store_id": str(seed_store.id),
            "date_from": BDATE.isoformat(),
            "date_to": BDATE.isoformat(),
            "hour_from": 2,
            "hour_to": 4,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_count"] == 0
    assert Decimal(body["revenue"]) == Decimal("0")
    assert Decimal(body["avg_ticket"]) == Decimal("0")


async def test_period_date_from_after_date_to_422(client, seed_store):
    """date_from > date_to → 422 VALIDATION_ERROR."""
    resp = await client.get(
        "/reports/period",
        params={
            "store_id": str(seed_store.id),
            "date_from": "2026-08-11",
            "date_to": "2026-08-10",
            "hour_from": 11,
            "hour_to": 14,
        },
    )
    assert resp.status_code == 422
    assert _err(resp.json())["code"] == "VALIDATION_ERROR"


# ──────────────────────────────────────────────────────────────────────────
# /reports/reconciliation
# ──────────────────────────────────────────────────────────────────────────


async def test_reconciliation_open_refund_cash(
    client, db_session, seed_tenant, seed_store, seed_menu_item,
):
    """Open order listed with amount; refund listed with reason; cash_expected
    equals the cash tender total of closed orders."""
    mi = seed_menu_item.id
    open_order = await _mk_order(
        db_session, seed_tenant, seed_store,
        status=OrderStatus.OPEN,
        lines=[(mi, "1", "180.00", None)],
    )
    await _mk_order(
        db_session, seed_tenant, seed_store,
        status=OrderStatus.REFUNDED,
        lines=[(mi, "1", "250.00", None)],
        refund_reason="上錯餐",
    )
    await _mk_order(
        db_session, seed_tenant, seed_store,
        lines=[(mi, "2", "200.00", None)],
        payments=[(PaymentMethod.CASH, "300.00"), (PaymentMethod.LINEPAY, "100.00")],
    )

    resp = await client.get(
        "/reports/reconciliation",
        params={"store_id": str(seed_store.id), "business_date": BDATE.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["open_orders"]) == 1
    assert body["open_orders"][0]["order_id"] == str(open_order.id)
    assert Decimal(body["open_orders"][0]["amount"]) == Decimal("180.00")

    assert len(body["refunds"]) == 1
    assert Decimal(body["refunds"][0]["amount"]) == Decimal("250.00")
    assert body["refunds"][0]["reason"] == "上錯餐"

    assert Decimal(body["cash_expected"]) == Decimal("300.00")
