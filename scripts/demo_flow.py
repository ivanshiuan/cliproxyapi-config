"""End-to-end demo flow.

Starts the FastAPI app in-process, then drives one full POS day:

    1. 員工上班打卡 (POST /clock/in)
    2. 顧客來客一張單 (POST /orders) — 2 漢堡 + 1 飲料 + comp 折扣
    3. 訂單收款 + 結帳 (POST /orders/{id}/close)
    4. 中途報廢 1 份食材 (POST /events/waste)
    5. 員工餐紀錄 (POST /events/staff-meal)
    6. 員工下班打卡 (POST /clock/out)
    7. 店員 POS 全流程: 開桌 -> 桌邊點餐 -> 現金結帳找零 -> 即時桌況事件 -> 日結報表 -> CSV 匯出
    8. 查詢當日 stock movements、orders、events 串起完整營運紀錄

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
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.main import app
from restaurant_api.models import (
    Employee,
    EmployeeRole,
    MenuItem,
    Order,
    OrderEvent,
    OrderLine,
    StaffMealEvent,
    StockMovement,
    Store,
    Tenant,
    TimeClock,
    WasteEvent,
)

_SEED_SLUG = "demo-restaurant"
_TPE = ZoneInfo("Asia/Taipei")


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
        _step(7, "POS 店員全流程：開桌 → 點餐 → 現金結帳 → 桌況事件 → 日結報表")
        biz_date = datetime.now(_TPE).date().isoformat()
        tname = f"D{datetime.now().strftime('%H%M%S')}"
        r = await client.post(
            "/tables", json={"store_id": store_id, "name": tname, "seats": 4}
        )
        if r.status_code != 201:
            _fail(f"create table failed: {r.status_code} {r.text[:300]}")
            return 1
        table = r.json()
        sess = (
            await client.post(f"/tables/{table['id']}/open", json={"party_size": 3})
        ).json()
        _ok(f"開桌 {tname} · session {sess['id'][:8]}…")

        for mid, qty, label in ((burger_id, "2", "漢堡"), (coke_id, "1", "可樂")):
            r = await client.post(
                f"/tables/sessions/{sess['id']}/lines",
                json={"menu_item_id": mid, "qty": qty},
            )
            if r.status_code != 201:
                _fail(f"add line ({label}) failed: {r.status_code} {r.text[:300]}")
                return 1
        bill = r.json()
        _ok(f"桌邊點餐：帳單 {len(bill['lines'])} 項（價格由菜單伺服器端快照）")

        co = await client.post(
            f"/tables/sessions/{sess['id']}/checkout", json={"tendered": "1000"}
        )
        if co.status_code != 200:
            _fail(f"checkout failed: {co.status_code} {co.text[:300]}")
            return 1
        cob = co.json()
        _ok(
            f"現金結帳：應收 {cob['total']} 收 1000 找零 {cob['change']}，"
            f"訂單={cob['order']['status']}、桌位自動結桌"
        )

        evs = (
            await client.get(
                "/tables/events", params={"store_id": store_id, "after": 0}
            )
        ).json()
        _info(f"即時桌況事件流 {len(evs)} 筆，末段：{[e['type'] for e in evs][-4:]}")

        rep = (
            await client.get(
                "/reports/sales", params={"store_id": store_id, "date_from": biz_date}
            )
        ).json()
        _ok(
            f"日結報表（{biz_date}）：{rep['order_count']} 單 · "
            f"淨營收 {rep['net_sales']} · 客單 {rep['avg_ticket']}"
        )
        _info(f"付款方式明細：{[(p['method'], p['amount']) for p in rep['payments']]}")

        csv_resp = await client.get(
            "/reports/export/orders.csv",
            params={"store_id": store_id, "date_from": biz_date},
        )
        _info(f"CSV 匯出可用：{csv_resp.status_code} · {len(csv_resp.content)} bytes")

        # ─────────────────────────────────────────────────────────────
        _step(8, "彙總：今日所有資料")
        await _report(store_id)

    await dispose_engine()
    print()
    print(
        "✅ End-to-end demo flow 完成。"
        "外送進單 → BOM → 報廢 → 員工餐 → 工時 ＋ 店員 POS（開桌→桌邊點餐→現金結帳→"
        "即時桌況事件→日結報表→CSV 匯出）全鏈跑通。"
    )
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
        n_events = (
            await s.execute(
                select(func.count(OrderEvent.id)).where(OrderEvent.store_id == store_id)
            )
        ).scalar_one()

    _info(f"orders       : {n_orders}")
    _info(f"order_lines  : {n_lines}")
    _info(f"stock_moves  : {n_movements}")
    _info(f"waste events : {n_waste}")
    _info(f"staff meals  : {n_staff_meals}")
    _info(f"time clocks  : {n_clocks}")
    _info(f"floor events : {n_events}")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run_demo()))
    except Exception as exc:
        print(f"❌ Demo flow failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
