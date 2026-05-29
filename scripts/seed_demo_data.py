"""Seed realistic demo data into the running Postgres.

Run after `make db-migrate`:

    python scripts/seed_demo_data.py [--reset]

Loads:
- 1 tenant, 1 store, 5 employees (owner / chef / 3 staff)
- 6 ingredients (with lot_no + expiry dates)
- 4 menu categories, 12 menu items (with allergens)
- recipes (1-3 ingredients per item)
- 3 sample customers (1 LINE-linked, 1 phone-only, 1 walk-in)
- a cash drawer session open at TWD 5000 float

Idempotent on slug — re-running just prints "already exists".
``--reset`` wipes the seed tenant first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.models import (
    CashDrawerSession,
    Customer,
    CustomerTier,
    Employee,
    EmployeeRole,
    Ingredient,
    MenuCategory,
    MenuItem,
    Recipe,
    Store,
    Tenant,
)

_SLUG = "demo-restaurant"
_NOW = datetime.now(UTC)
_TODAY = date.today()


_EMPLOYEES = [
    {"name": "王老闆", "role": EmployeeRole.OWNER, "wage": "0.00"},
    {"name": "陳店長", "role": EmployeeRole.MANAGER, "wage": "280.00"},
    {"name": "李主廚", "role": EmployeeRole.CHEF, "wage": "320.00"},
    {"name": "張小芳", "role": EmployeeRole.STAFF, "wage": "200.00"},
    {"name": "黃志明", "role": EmployeeRole.STAFF, "wage": "200.00"},
]

_INGREDIENTS = [
    {"name": "牛胸肉", "unit": "g", "cost": "0.4500", "lot": "BF-20260520", "days": 3},
    {"name": "雞胸肉", "unit": "g", "cost": "0.2800", "lot": "CH-20260524", "days": 2},
    {"name": "麵粉", "unit": "g", "cost": "0.0300", "lot": "FL-20260501", "days": 90},
    {"name": "起司片", "unit": "piece", "cost": "8.0000", "lot": "CS-20260510", "days": 14},
    {"name": "生菜", "unit": "g", "cost": "0.1200", "lot": "LT-20260525", "days": 5},
    {"name": "番茄醬", "unit": "ml", "cost": "0.0500", "lot": "KT-20260101", "days": 180},
]

_CATEGORIES = ["主餐", "副餐", "飲料", "甜點"]

_MENU_ITEMS = [
    {"name": "經典牛肉漢堡", "sku": "BURG-BEEF-01", "cat": 0, "price": "280.00",
     "recipe": [(0, "120"), (2, "80"), (3, "1"), (4, "20"), (5, "10")],
     "allergens": ["wheat", "milk", "egg"]},
    {"name": "招牌雞肉漢堡", "sku": "BURG-CHKN-01", "cat": 0, "price": "240.00",
     "recipe": [(1, "150"), (2, "80"), (4, "20"), (5, "10")],
     "allergens": ["wheat", "egg"]},
    {"name": "雙倍起司牛堡", "sku": "BURG-BEEF-02", "cat": 0, "price": "320.00",
     "recipe": [(0, "120"), (2, "80"), (3, "2"), (4, "20")],
     "allergens": ["wheat", "milk"]},
    {"name": "薯條 (中)", "sku": "SIDE-FRY-M", "cat": 1, "price": "80.00",
     "recipe": [], "allergens": []},
    {"name": "雞塊 (6 塊)", "sku": "SIDE-NUG-6", "cat": 1, "price": "120.00",
     "recipe": [(1, "90")], "allergens": ["wheat", "egg"]},
    {"name": "凱薩沙拉", "sku": "SIDE-SAL-CSR", "cat": 1, "price": "150.00",
     "recipe": [(4, "80"), (3, "1")], "allergens": ["milk", "egg", "fish"]},
    {"name": "可樂 (中)", "sku": "DRK-COKE-M", "cat": 2, "price": "50.00",
     "recipe": [], "allergens": []},
    {"name": "現榨柳橙汁", "sku": "DRK-OJ", "cat": 2, "price": "90.00",
     "recipe": [], "allergens": []},
    {"name": "美式咖啡", "sku": "DRK-COFFEE", "cat": 2, "price": "70.00",
     "recipe": [], "allergens": []},
    {"name": "巧克力布朗尼", "sku": "DSRT-BRWN", "cat": 3, "price": "120.00",
     "recipe": [(2, "60"), (3, "1")], "allergens": ["wheat", "milk", "egg"]},
    {"name": "焦糖布丁", "sku": "DSRT-CRMBR", "cat": 3, "price": "100.00",
     "recipe": [(3, "1")], "allergens": ["milk", "egg"]},
    {"name": "草莓冰淇淋", "sku": "DSRT-ICE-STRB", "cat": 3, "price": "90.00",
     "recipe": [(3, "1")], "allergens": ["milk"]},
]

_CUSTOMERS = [
    {"name": "林先生", "phone": "0912-345-678", "email": "lin@example.com",
     "line_user_id": "U_LIN_DEMO_01", "tier": CustomerTier.GOLD,
     "lifetime_value": "12500.00", "points": "250.00"},
    {"name": "陳小姐", "phone": "0923-456-789", "email": None,
     "line_user_id": None, "tier": CustomerTier.SILVER,
     "lifetime_value": "4800.00", "points": "96.00"},
    {"name": "外帶客", "phone": None, "email": None,
     "line_user_id": None, "tier": CustomerTier.REGULAR,
     "lifetime_value": "350.00", "points": "0.00"},
]


async def _reset_seed(session) -> None:
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == _SLUG))
    ).scalar_one_or_none()
    if existing is None:
        return
    print(f"  · resetting tenant {existing.id} ...")

    from restaurant_api.models import (
        CustomerPointsLedger,
        LeaveRequest,
        Order,
        OrderDiscount,
        OrderLine,
        OrderPayment,
        Shift,
        StaffMealEvent,
        StockMovement,
        TastingEvent,
        TimeClock,
        WasteEvent,
    )
    from restaurant_api.models import (
        Recipe as RecipeModel,
    )

    table_order = [
        OrderDiscount, OrderPayment, OrderLine, Order,
        WasteEvent, StaffMealEvent, TastingEvent,
        TimeClock, Shift, LeaveRequest,
        CustomerPointsLedger, Customer,
        StockMovement, RecipeModel,
        MenuItem, MenuCategory,
        Ingredient, CashDrawerSession,
        Employee, Store,
    ]
    for model in table_order:
        if hasattr(model, "tenant_id"):
            await session.execute(delete(model).where(model.tenant_id == existing.id))
    await session.execute(delete(Tenant).where(Tenant.id == existing.id))
    await session.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Wipe seed tenant first")
    args = parser.parse_args()

    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session, session.begin():
        if args.reset:
            await _reset_seed(session)

        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == _SLUG))
        ).scalar_one_or_none()
        if existing is not None and not args.reset:
            print(f"✅ Seed tenant already exists: {existing.id} (use --reset to wipe)")
            return 0

        tenant = Tenant(name="王老闆漢堡店", slug=_SLUG)
        session.add(tenant)
        await session.flush()
        print(f"  + tenant     : {tenant.name} ({tenant.id})")

        store = Store(
            tenant_id=tenant.id,
            name="信義旗艦店",
            address="台北市信義區市府路 1 號",
            phone="02-2720-1234",
            google_place_id="ChIJ_demo_seed_data_xx",
            latitude=Decimal("25.0339"),
            longitude=Decimal("121.5645"),
            opened_on=date(2026, 6, 1),
            timezone="Asia/Taipei",
            is_active=True,
        )
        session.add(store)
        await session.flush()
        print(f"  + store      : {store.name} ({store.id})")

        emp_ids: list = []
        for e in _EMPLOYEES:
            emp = Employee(
                tenant_id=tenant.id,
                store_id=store.id,
                full_name=e["name"],
                role=e["role"],
                hourly_wage=Decimal(e["wage"]),
                hired_on=date(2026, 1, 1),
                is_active=True,
            )
            session.add(emp)
            await session.flush()
            emp_ids.append(emp.id)
        print(f"  + employees  : {len(_EMPLOYEES)}")

        ing_ids: list = []
        for ing in _INGREDIENTS:
            i = Ingredient(
                tenant_id=tenant.id,
                store_id=store.id,
                name=ing["name"],
                unit=ing["unit"],
                standard_cost_per_unit=Decimal(ing["cost"]),
                lot_no=ing["lot"],
                expiry_date=_TODAY + timedelta(days=ing["days"]),
                is_active=True,
            )
            session.add(i)
            await session.flush()
            ing_ids.append(i.id)
        print(f"  + ingredients: {len(_INGREDIENTS)} (含批號、效期)")

        cat_ids: list = []
        for idx, name in enumerate(_CATEGORIES):
            c = MenuCategory(
                tenant_id=tenant.id, store_id=store.id, name=name,
                sort_order=idx, is_active=True,
            )
            session.add(c)
            await session.flush()
            cat_ids.append(c.id)

        for mi in _MENU_ITEMS:
            item = MenuItem(
                tenant_id=tenant.id,
                store_id=store.id,
                category_id=cat_ids[mi["cat"]],
                sku=mi["sku"],
                name=mi["name"],
                price=Decimal(mi["price"]),
                is_available=True,
                allergens=mi["allergens"],
            )
            session.add(item)
            await session.flush()
            for ing_idx, qty in mi["recipe"]:
                session.add(
                    Recipe(
                        tenant_id=tenant.id,
                        menu_item_id=item.id,
                        ingredient_id=ing_ids[ing_idx],
                        qty_per_serving=Decimal(qty),
                        effective_from=_TODAY,
                    )
                )
        print(f"  + menu       : {len(_CATEGORIES)} categories, {len(_MENU_ITEMS)} items")

        for c in _CUSTOMERS:
            session.add(
                Customer(
                    tenant_id=tenant.id,
                    display_name=c["name"],
                    phone=c["phone"],
                    email=c["email"],
                    line_user_id=c["line_user_id"],
                    tier=c["tier"],
                    lifetime_value=Decimal(c["lifetime_value"]),
                    points_balance=Decimal(c["points"]),
                )
            )
        print(f"  + customers  : {len(_CUSTOMERS)}")

        session.add(
            CashDrawerSession(
                tenant_id=tenant.id,
                store_id=store.id,
                register_no="REG-01",
                opened_at=_NOW,
                opened_by=emp_ids[1],
                opening_float=Decimal("5000.00"),
            )
        )
        print("  + cash drawer: opened with TWD 5000 float")

    await dispose_engine()
    print()
    print("✅ Demo data seeded. Try: `make api`, then visit http://localhost:8000/docs")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"❌ Seed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
