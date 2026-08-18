"""Lifecycle tests for the extended ``/orders`` surface (G4-G7).

Covers the haodian-parity additions:
- create with ``service_type`` / ``order_source`` / ``table_id``
- line ``modifiers`` priced into ``line_total`` (Decimal all the way)
- ``GET /orders`` list with status / source filters
- ``POST /orders/{id}/lines`` (加點, open-only)
- ``POST /orders/{id}/refund`` (closed-only, audit-logged)

Uses the shared conftest fixtures (`client` = httpx.AsyncClient over
ASGITransport, savepoint-rolled `db_session`). Queries are always scoped
to this test's seeded order/store ids — never whole-table scans.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import AuditLog, DiningTable, Order, Store

# Ensure the router is mounted even if main's explicit wiring changes.
importlib.import_module("restaurant_api.routers.orders")


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _err(body: dict[str, Any]) -> dict[str, Any]:
    """Pluck the inner error dict regardless of envelope flavour."""
    if "error" in body:
        return body["error"]
    if "detail" in body and isinstance(body["detail"], dict):
        return body["detail"]
    return body


def _base_body(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "store_id": str(store_id),
        "order_no": f"LC-{uuid.uuid4().hex[:8]}",
        "business_date": date.today().isoformat(),
    }
    body.update(overrides)
    return body


def _line(menu_item_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "menu_item_id": str(menu_item_id),
        "qty": "1",
        "unit_price": "100.00",
    }
    line.update(overrides)
    return line


async def _seed_table(
    db_session: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> DiningTable:
    table = DiningTable(
        tenant_id=tenant_id,
        store_id=store_id,
        name=f"A{uuid.uuid4().hex[:6]}",
        capacity=4,
    )
    db_session.add(table)
    await db_session.flush()
    return table


async def _create_order(client, store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    resp = await client.post("/orders", json=_base_body(store_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _close_order(client, order_id: str) -> dict[str, Any]:
    resp = await client.post(f"/orders/{order_id}/close", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────
# Create — service_type / order_source / table_id
# ──────────────────────────────────────────────────────────────────────────


async def test_create_defaults_dine_in_pos(client, seed_store):
    """Omitting the new fields defaults to dine_in / pos / no table."""
    data = await _create_order(client, seed_store.id)
    assert (data["service_type"], data["order_source"], data["table_id"]) == (
        "dine_in", "pos", None,
    )


async def test_create_with_service_type_source_and_table(
    client, db_session: AsyncSession, seed_tenant, seed_store
):
    """Explicit service_type/order_source/table_id round-trip on the response."""
    table = await _seed_table(db_session, seed_tenant.id, seed_store.id)
    data = await _create_order(
        client,
        seed_store.id,
        service_type="dine_in",
        order_source="qr",
        table_id=str(table.id),
    )
    assert (data["service_type"], data["order_source"], data["table_id"]) == (
        "dine_in", "qr", str(table.id),
    )


async def test_create_with_unknown_table_404(client, seed_store):
    """A table_id that doesn't exist is a 404 NotFoundError."""
    resp = await client.post(
        "/orders",
        json=_base_body(seed_store.id, table_id=str(uuid.uuid4())),
    )
    assert resp.status_code == 404
    assert _err(resp.json())["code"] == "NOT_FOUND"


async def test_create_with_other_stores_table_422(
    client, db_session: AsyncSession, seed_tenant, seed_store
):
    """A real table belonging to a different store is a 422 ValidationError."""
    other_store = Store(
        tenant_id=seed_tenant.id,
        name="Other Store",
        address="Elsewhere",
        opened_on=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(other_store)
    await db_session.flush()
    foreign_table = await _seed_table(db_session, seed_tenant.id, other_store.id)

    resp = await client.post(
        "/orders",
        json=_base_body(seed_store.id, table_id=str(foreign_table.id)),
    )
    assert resp.status_code == 422
    assert _err(resp.json())["code"] == "VALIDATION_ERROR"


# ──────────────────────────────────────────────────────────────────────────
# Modifiers pricing
# ──────────────────────────────────────────────────────────────────────────


async def test_modifiers_priced_into_line_total(client, seed_store, seed_menu_item):
    """line_total = (unit_price + Σ price_delta) x qty, exact Decimal."""
    data = await _create_order(
        client,
        seed_store.id,
        lines=[
            _line(
                seed_menu_item.id,
                qty="2",
                unit_price="50.00",
                modifiers=[
                    {"group": "加料", "option": "珍珠", "price_delta": "10.00"},
                    {"group": "折抵", "option": "自帶杯", "price_delta": "-5.00"},
                ],
            )
        ],
    )
    line = data["lines"][0]
    # (50 + 10 - 5) * 2 = 110 — compare as Decimal to dodge formatting noise.
    assert Decimal(line["line_total"]) == Decimal("110.00")
    assert [m["option"] for m in line["modifiers"]] == ["珍珠", "自帶杯"]


async def test_modifier_float_price_delta_rejected(client, seed_store, seed_menu_item):
    """Bare float on modifiers.price_delta is 422'd (money is never float)."""
    resp = await client.post(
        "/orders",
        json=_base_body(
            seed_store.id,
            lines=[
                _line(
                    seed_menu_item.id,
                    modifiers=[{"group": "加料", "option": "珍珠", "price_delta": 10.5}],
                )
            ],
        ),
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# GET /orders — list + filters
# ──────────────────────────────────────────────────────────────────────────


async def test_list_filter_status_open(client, seed_store, seed_menu_item):
    """status=open returns only the still-open order (未結單)."""
    open_order = await _create_order(
        client, seed_store.id, lines=[_line(seed_menu_item.id)]
    )
    closed_order = await _create_order(client, seed_store.id)
    await _close_order(client, closed_order["id"])

    resp = await client.get(
        "/orders", params={"store_id": str(seed_store.id), "status": "open"}
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [open_order["id"]]


async def test_list_filter_status_closed_with_total(client, seed_store, seed_menu_item):
    """status=closed returns the closed order with its Σ line_total."""
    order = await _create_order(
        client, seed_store.id, lines=[_line(seed_menu_item.id, qty="3", unit_price="80.00")]
    )
    await _close_order(client, order["id"])
    await _create_order(client, seed_store.id)  # stays open, must be filtered out

    resp = await client.get(
        "/orders", params={"store_id": str(seed_store.id), "status": "closed"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [order["id"]]
    assert Decimal(items[0]["total"]) == Decimal("240.00")


async def test_list_filter_order_source(client, seed_store):
    """order_source=line narrows to LINE-channel orders only."""
    line_order = await _create_order(client, seed_store.id, order_source="line")
    await _create_order(client, seed_store.id, order_source="pos")

    resp = await client.get(
        "/orders", params={"store_id": str(seed_store.id), "order_source": "line"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [line_order["id"]]
    assert items[0]["order_source"] == "line"


# ──────────────────────────────────────────────────────────────────────────
# POST /orders/{id}/lines — 加點
# ──────────────────────────────────────────────────────────────────────────


async def test_add_lines_to_open_order_updates_totals(
    client, seed_store, seed_menu_item
):
    """加點 appends the line and the modifier-adjusted total shows up."""
    order = await _create_order(
        client, seed_store.id, lines=[_line(seed_menu_item.id, unit_price="100.00")]
    )
    resp = await client.post(
        f"/orders/{order['id']}/lines",
        json={
            "lines": [
                _line(
                    seed_menu_item.id,
                    qty="2",
                    unit_price="60.00",
                    modifiers=[{"group": "加料", "option": "起司", "price_delta": "15.00"}],
                )
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["lines"]) == 2
    totals = sorted(Decimal(ln["line_total"]) for ln in data["lines"])
    assert totals == [Decimal("100.00"), Decimal("150.00")]  # (60+15)*2 = 150


async def test_add_lines_to_closed_order_409(client, seed_store, seed_menu_item):
    """加點 on a closed order conflicts — only open orders take new lines."""
    order = await _create_order(client, seed_store.id)
    await _close_order(client, order["id"])

    resp = await client.post(
        f"/orders/{order['id']}/lines",
        json={"lines": [_line(seed_menu_item.id)]},
    )
    assert resp.status_code == 409
    assert _err(resp.json())["code"] == "CONFLICT"


# ──────────────────────────────────────────────────────────────────────────
# POST /orders/{id}/refund
# ──────────────────────────────────────────────────────────────────────────


async def test_refund_closed_order_sets_refund_trail(
    client, db_session: AsyncSession, seed_store
):
    """closed → refunded stamps refunded_at + refund_reason (and persists)."""
    order = await _create_order(client, seed_store.id)
    await _close_order(client, order["id"])

    resp = await client.post(
        f"/orders/{order['id']}/refund", json={"reason": "客訴 餐點有異物"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "refunded"
    assert data["refunded_at"] is not None
    assert data["refund_reason"] == "客訴 餐點有異物"

    row = await db_session.get(Order, uuid.UUID(order["id"]))
    assert row is not None and row.refunded_at is not None


async def test_refund_open_order_409(client, seed_store):
    """Refunding an order that was never closed conflicts."""
    order = await _create_order(client, seed_store.id)
    resp = await client.post(f"/orders/{order['id']}/refund", json={"reason": "太快了"})
    assert resp.status_code == 409
    assert _err(resp.json())["code"] == "CONFLICT"


async def test_refund_without_reason_422(client, seed_store):
    """reason is mandatory — an empty body is a 422."""
    order = await _create_order(client, seed_store.id)
    await _close_order(client, order["id"])
    resp = await client.post(f"/orders/{order['id']}/refund", json={})
    assert resp.status_code == 422


async def test_refund_writes_audit_log_row(
    client, db_session: AsyncSession, seed_store
):
    """The refund leaves an ``order.refunded`` audit row targeting this order."""
    order = await _create_order(client, seed_store.id)
    await _close_order(client, order["id"])
    resp = await client.post(f"/orders/{order['id']}/refund", json={"reason": "重複收款"})
    assert resp.status_code == 200

    stmt = select(AuditLog).where(
        AuditLog.target_table == "orders",
        AuditLog.target_id == uuid.UUID(order["id"]),
        AuditLog.action == "order.refunded",
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == "重複收款"
