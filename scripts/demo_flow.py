"""End-to-end demo flow.

Starts the FastAPI app in-process, then drives one full POS day:

    1. 員工上班打卡 (POST /clock/in)
    2. 顧客來客一張單 (POST /orders) — 2 漢堡 + 1 飲料 + comp 折扣
    3. 訂單收款 + 結帳 (POST /orders/{id}/close)
    4. 中途報廢 1 份食材 (POST /events/waste)
    5. 員工餐紀錄 (POST /events/staff-meal)
    6. 員工下班打卡 (POST /clock/out)
    7. 查詢當日 stock movements、orders、events 串起完整營運紀錄

Requires:
    - Postgres reachable per .env
    - `scripts/seed_demo_data.py` previously run

Run: `make demo-flow` or `python scripts/demo_flow.py`
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.main import app
from restaurant_api.models import (
    Employee,
    EmployeeRole,
    MenuItem,
    Order,
    OrderLine,
    StaffMealEvent,
    StockMovement,
    Store,
    Tenant,
    TimeClock,
    WasteEvent,
)

_SEED_SLUG = "demo-restaurant"


# ──────────────────────────────────────────────────────────────────────────
# Output helpers
# ──────────────────────────────────────────────────────────────────────────


def _step(n: int, title: str) -> None:
    print()
    print(f"━━━ Step {n} — {title} ━━━")


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _info(msg: str) -> None:
    print(f"  · {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────
# Setup: pull seed entities
# ──────────────────────────────────────────────────────────────────────────


async def _load_context(session) -> dict[str, Any]:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == _SEED_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        _fail("Seed tenant not found. Run `python scripts/seed_demo_data.py` first.")
        sys.exit(1)

    store = (
        await session.execute(select(Store).where(Store.tenant_id == tenant.id))
    ).scalar_one()
    employees = (
        (await session.execute(select(Employee).where(Employee.tenant_id == tenant.id)))
        .scalars().all()
    )
    employee = next(e for e in employees if e.role == EmployeeRole.STAFF)
    menu_items = (
        (
            await session.execute(
                select(MenuItem).where(MenuItem.tenant_id == tenant.id).limit(50)
            )
        )
        .scalars()
        .all()
    )
    sku_map = {m.sku: m for m in menu_items}

    return {
        "tenant": tenant,
        "store": store,
        "employee": employee,
        "burger": sku_map["BURG-BEEF-01"],
        "fries": sku_map["SIDE-FRY-M"],
        "coke": sku_map["DRK-COKE-M"],
    }


# ──────────────────────────────────────────────────────────────────────────
# Run the demo
# ──────────────────────────────────────────────────────────────────────────


async def run_demo() -> int:
    SessionLocal = get_sessionmaker()

    async with SessionLocal() as session:
        ctx = await _load_context(session)

    store_id = str(ctx["store"].id)
    employee_id = str(ctx["employee"].id)
    burger_id = str(ctx["burger"].id)
    coke_id = str(ctx["coke"].id)
    employee_name = ctx["employee"].full_name

    # Override the tenant DI to the seed tenant so /orders/{id} reads find
    # the rows we just inserted instead of 404ing under the all-zero default.
    from restaurant_api.api.deps import get_current_tenant_id

    def _seed_tenant() -> Any:
        return ctx["tenant"].id

    app.dependency_overrides[get_current_tenant_id] = _seed_tenant

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # ─────────────────────────────────────────────────────────────
        _step(1, f"員工上班打卡：{employee_name}")
        r = await client.post(
            "/clock/in",
            json={"employee_id": employee_id, "store_id": store_id},
        )
        if r.status_code not in (200, 201, 409):
            _fail(f"clock-in failed: {r.status_code} {r.text}")
            return 1
        if r.status_code == 409:
            _info("已在班 — 略過 clock-in")
        else:
            _ok(f"clock_in_at = {r.json().get('clock_in_at', '?')}")

        # ─────────────────────────────────────────────────────────────
        _step(2, "建立訂單：2 漢堡 + 1 可樂 + 5% 折扣")
        order_no = f"DEMO-{datetime.now().strftime('%H%M%S')}"
        order_payload = {
            "store_id": store_id,
            "order_no": order_no,
            "business_date": datetime.now().strftime("%Y-%m-%d"),
            "external_pos_id": order_no,
            "pos_source": "manual",
            "lines": [
                {"menu_item_id": burger_id, "qty": "2",
                 "unit_price": "280.00"},
                {"menu_item_id": coke_id, "qty": "1",
                 "unit_price": "50.00"},
            ],
            "discounts": [
                {"kind": "percent", "value": "0.05", "reason": "VIP"},
            ],
            "buyer_tax_id": None,
            "carrier_type": "mobile",
            "carrier_id": "/ABC1234",
        }
        r = await client.post("/orders", json=order_payload)
        if r.status_code not in (200, 201):
            _fail(f"create order failed: {r.status_code} {r.text[:300]}")
            return 1
        order = r.json()
        order_id = order["id"]
        _ok(f"order {order_id[:8]}… 已開立，狀態 = {order['status']}")
        _info(f"買方載具 = {order.get('carrier_type')}/{order.get('carrier_id')}")

        # ─────────────────────────────────────────────────────────────
        _step(3, "結帳關單")
        r = await client.post(f"/orders/{order_id}/close", json={})
        if r.status_code != 200:
            _fail(f"close failed: {r.status_code} {r.text[:300]}")
            return 1
        closed = r.json()
        _ok(f"狀態轉為 {closed['status']}，closed_at = {closed.get('closed_at', '?')}")

        # ─────────────────────────────────────────────────────────────
        _step(4, "報廢事件：薯條 100g (過期)")
        r = await client.post(
            "/events/waste",
            json={
                "store_id": store_id,
                "ingredient_id": str(
                    await _first_ingredient_id(ctx["store"].tenant_id)
                ),
                "qty": "100",
                "cost": "30.00",
                "reason": "spoiled",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
        if r.status_code not in (200, 201):
            _fail(f"waste event failed: {r.status_code} {r.text[:300]}")
            return 1
        _ok("報廢事件已寫入；stock_movements 同步寫負 100g")

        # ─────────────────────────────────────────────────────────────
        _step(5, "員工餐紀錄：1 份雞肉漢堡")
        nugget_id = await _get_menu_id_by_sku(ctx["store"].tenant_id, "BURG-CHKN-01")
        r = await client.post(
            "/events/staff-meal",
            json={
                "store_id": store_id,
                "employee_id": employee_id,
                "menu_item_id": str(nugget_id),
                "total_cost": "80.00",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
        if r.status_code not in (200, 201):
            _fail(f"staff meal failed: {r.status_code} {r.text[:300]}")
            return 1
        _ok("員工餐已紀錄，從成本端拆出（不算營收）")

        # ─────────────────────────────────────────────────────────────
        _step(6, "員工下班打卡（補一個 9 小時的場景）")
        async with SessionLocal() as s2:
            tc = (
                await s2.execute(
                    select(TimeClock)
                    .where(TimeClock.employee_id == ctx["employee"].id)
                    .where(TimeClock.clock_out.is_(None))
                    .order_by(TimeClock.clock_in.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if tc is not None:
                tc.clock_in = datetime.now(UTC) - timedelta(hours=9)
                await s2.commit()
                _info("把 clock_in 回推 9 小時，營造一個完整工作日")

        r = await client.post(
            "/clock/out",
            json={"employee_id": employee_id},
        )
        if r.status_code != 200:
            _fail(f"clock-out failed: {r.status_code} {r.text[:300]}")
            return 1
        out = r.json()
        _ok(
            f"下班：regular={out.get('regular_hours')}h "
            f"OT1={out.get('overtime_tier1_hours')}h "
            f"OT2={out.get('overtime_tier2_hours')}h "
            f"holiday={out.get('holiday_hours')}h"
        )

        # ─────────────────────────────────────────────────────────────
        _step(7, "彙總：今日所有資料")
        await _report(store_id)

    await dispose_engine()
    print()
    print("✅ End-to-end demo flow 完成。完整的 POS → BOM → 報廢 → 員工餐 → 工時 鏈跑通了。")
    return 0


async def _first_ingredient_id(tenant_id) -> Any:
    from restaurant_api.models import Ingredient

    SessionLocal = get_sessionmaker()
    async with SessionLocal() as s:
        return (
            (
                await s.execute(
                    select(Ingredient.id).where(Ingredient.tenant_id == tenant_id).limit(1)
                )
            ).scalar_one()
        )


async def _get_menu_id_by_sku(tenant_id, sku: str) -> Any:
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as s:
        return (
            (
                await s.execute(
                    select(MenuItem.id)
                    .where(MenuItem.tenant_id == tenant_id)
                    .where(MenuItem.sku == sku)
                )
            ).scalar_one()
        )


async def _report(store_id: str) -> None:
    """Read back what just happened — proves the loop closed."""
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as s:
        n_orders = (
            await s.execute(
                select(func.count(Order.id)).where(Order.store_id == store_id)
            )
        ).scalar_one()
        n_lines = (
            await s.execute(
                select(func.count(OrderLine.id)).join(Order, OrderLine.order_id == Order.id)
                .where(Order.store_id == store_id)
            )
        ).scalar_one()
        n_movements = (
            await s.execute(
                select(func.count(StockMovement.id)).where(StockMovement.store_id == store_id)
            )
        ).scalar_one()
        n_waste = (
            await s.execute(
                select(func.count(WasteEvent.id)).where(WasteEvent.store_id == store_id)
            )
        ).scalar_one()
        n_staff_meals = (
            await s.execute(
                select(func.count(StaffMealEvent.id)).where(StaffMealEvent.store_id == store_id)
            )
        ).scalar_one()
        n_clocks = (
            await s.execute(
                select(func.count(TimeClock.id)).where(TimeClock.store_id == store_id)
            )
        ).scalar_one()

    _info(f"orders       : {n_orders}")
    _info(f"order_lines  : {n_lines}")
    _info(f"stock_moves  : {n_movements}")
    _info(f"waste events : {n_waste}")
    _info(f"staff meals  : {n_staff_meals}")
    _info(f"time clocks  : {n_clocks}")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run_demo()))
    except Exception as exc:
        print(f"❌ Demo flow failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
