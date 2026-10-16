"""``GET /reports/daily-pnl`` end to end: real order/close services, real Postgres, real HTTP.

The orders go through the same API the POS uses (so COGS comes from the real BOM engine and the
discount stack from the real resolver). Expected numbers were computed independently of the code.
Everything is scoped to the fixture store, so seed/demo rows already in the dev DB cannot interfere.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from restaurant_api.models import (
    Employee,
    Order,
    StaffMealEvent,
    Store,
    TastingEvent,
    TimeClock,
    WasteEvent,
)
from restaurant_api.models.employees import EmployeeRole

DAY = date(2026, 10, 14)  # a 31-day month
TPE = ZoneInfo("Asia/Taipei")
URL = "/reports/daily-pnl"


@pytest.fixture(autouse=True)
def _pnl_env(monkeypatch):
    for name in ("PRICES_INCLUDE_VAT", "VAT_RATE", "LABOR_ON_COST_RATE"):
        monkeypatch.delenv(f"RESTO_PNL_{name}", raising=False)
    monkeypatch.setenv("RESTO_PNL_FIXED_COSTS_MONTHLY", json.dumps({"rent-租金": 62000, "utilities-水電": 31000}))


async def _order(client, store, item, *, qty, discounts=None, payments=None, close=True) -> str:
    body = {
        "store_id": str(store.id),
        "order_no": f"PNL-{uuid.uuid4().hex[:8]}",
        "business_date": DAY.isoformat(),
        "lines": [{"menu_item_id": str(item.id), "qty": str(qty), "unit_price": "250"}],
        "discounts": discounts or [],
        "payments": payments or [],
    }
    r = await client.post("/orders", json=body)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    if close:
        c = await client.post(f"/orders/{oid}/close", json={})
        assert c.status_code == 200, c.text
    return oid


@pytest.fixture
async def scenario(client, db_session, seed_tenant, seed_store, seed_menu_item, seed_recipe, seed_employee, monkeypatch):
    """Two closed orders (one with 10% off), one open, one voided; costs; two staff; a 10% service charge."""
    # The service-charge column arrives with PR #29; until then simulate it so the loader's path is covered.
    monkeypatch.setattr(Order, "service_charge_rate", D("0.10"), raising=False)

    await _order(  # 4 x 250 = 1000, 10% off -> 900, service 10% of 1000 = 100, due 1000
        client, seed_store, seed_menu_item, qty=4,
        discounts=[{"kind": "percent", "value": "0.10"}],
        payments=[{"method": "cash", "amount": "1000"}],
    )
    await _order(  # 2 x 250 = 500, no discount, service 50, due 550, card fee 8.25
        client, seed_store, seed_menu_item, qty=2,
        payments=[{"method": "credit", "amount": "550", "fee_amount": "8.25"}],
    )
    await _order(client, seed_store, seed_menu_item, qty=9, close=False)  # still open: must not count
    voided = await _order(client, seed_store, seed_menu_item, qty=9)  # closed then voided: must not count
    v = await client.post(f"/orders/{voided}/void", json={"reason": "wrong table"})
    assert v.status_code == 200, v.text

    noon = datetime(2026, 10, 14, 12, 0, tzinfo=TPE)
    db_session.add_all(
        [
            WasteEvent(tenant_id=seed_tenant.id, store_id=seed_store.id, ingredient_id=seed_recipe.ingredient_id,
                       qty=D("1"), cost=D("25"), reason="expired", occurred_at=noon),
            StaffMealEvent(tenant_id=seed_tenant.id, store_id=seed_store.id, employee_id=seed_employee.id,
                           total_cost=D("40"), occurred_at=noon),
            TastingEvent(tenant_id=seed_tenant.id, store_id=seed_store.id, purpose="new menu",
                         total_cost=D("10"), occurred_at=noon),
            # hourly staff (200/h): 8 regular + 1 overtime(1.34) -> 1868.00
            TimeClock(tenant_id=seed_tenant.id, store_id=seed_store.id, employee_id=seed_employee.id,
                      clock_in=noon, clock_out=datetime(2026, 10, 14, 21, 0, tzinfo=TPE),
                      regular_hours=D("8"), overtime_tier1_hours=D("1")),
        ]
    )
    manager = Employee(
        tenant_id=seed_tenant.id, store_id=seed_store.id, full_name="store-manager", role=EmployeeRole.MANAGER,
        hourly_wage=D("0"), monthly_salary=D("62000"), hired_on=date(2026, 1, 1),
    )
    db_session.add(manager)
    await db_session.flush()
    # a shift that never ended: reported, not guessed
    db_session.add(TimeClock(tenant_id=seed_tenant.id, store_id=seed_store.id, employee_id=manager.id, clock_in=noon))
    await db_session.flush()
    return seed_store


async def test_full_day_from_real_orders(client, scenario):
    r = await client.get(URL, params={"store_id": str(scenario.id), "date": DAY.isoformat()})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    j = {k: (D(v) if isinstance(v, str) and k not in ("store_id", "store_name", "business_date") else v)
         for k, v in r.json().items()}

    assert (j["order_count"], j["open_order_count"], j["voided_order_count"]) == (2, 1, 1)

    # revenue: the discount is resolved PER ORDER. A naive one-shot call would take 10% of the whole
    # day's 1500 (= 150) instead of 10% of the one discounted order's 1000 (= 100).
    assert j["menu_gross"] == D("1500.00")
    assert j["discount_total"] == D("100.00")
    assert j["service_charge"] == D("150.00")
    assert j["billed_incl_vat"] == D("1550.00")
    assert j["vat"] == D("73.81")
    assert j["revenue_ex_vat"] == D("1476.19")

    # costs: BOM-derived (6 burgers x 40) + the three event types + the card fee
    assert j["cogs"] == D("240.00")
    assert (j["cost_waste"], j["cost_staff_meal"], j["cost_tasting"]) == (D("25.00"), D("40.00"), D("10.00"))
    assert j["payment_fees"] == D("8.25")
    assert j["gross_profit"] == D("1161.19")

    # people and rent: 1868.00 hourly + 62000/31 salaried accrual; (62000 + 31000)/31 fixed
    assert j["labor_cost"] == D("3868.00")
    assert j["fixed_cost_total"] == D("3000.00")
    assert j["net_profit"] == D("-5715.06")

    # reconciliation: cash 1000 + card 550 == the till's 1550
    assert j["payments_total"] == D("1550.00") and j["payments_diff"] == D("0.00")
    warnings = " ".join(r.json()["warnings"])
    assert "1 筆打卡還沒下班" in warnings and "1 張訂單沒結帳" in warnings and "1 張訂單已作廢" in warnings
    assert "收款合計" not in warnings and "服務費合計是 0" not in warnings


async def test_without_the_service_charge_column_it_is_zero_and_says_so(client, scenario, monkeypatch):
    """On a schema without Order.service_charge_rate nothing is invented: charge 0 + a visible warning."""
    monkeypatch.delattr(Order, "service_charge_rate")
    r = await client.get(URL, params={"store_id": str(scenario.id), "date": DAY.isoformat()})
    body = r.json()
    assert D(body["service_charge"]) == D("0.00") and D(body["billed_incl_vat"]) == D("1400.00")
    assert any("服務費合計是 0" in w for w in body["warnings"])
    assert any("收款合計" in w for w in body["warnings"])  # cash collected (1550) != due (1400): flagged


async def test_html_page(client, scenario):
    r = await client.get(URL, params={"store_id": str(scenario.id), "date": DAY.isoformat(), "format": "html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert "今日估算淨利" in r.text and "Test Store" in r.text and "-5,715" in r.text
    assert "<script" not in r.text


async def test_requires_admin(anon_client, seed_store):
    r = await anon_client.get(URL, params={"store_id": str(seed_store.id)})
    assert r.status_code == 401


async def test_unknown_store_is_404(client):
    r = await client.get(URL, params={"store_id": str(uuid.uuid4()), "date": DAY.isoformat()})
    assert r.status_code == 404


async def test_store_must_be_named_when_there_are_several(client, db_session, seed_store, seed_tenant):
    db_session.add(Store(tenant_id=seed_tenant.id, name="Second Store", address="x", phone="0", opened_on=DAY, is_active=True))
    await db_session.flush()
    r = await client.get(URL, params={"date": DAY.isoformat()})
    assert r.status_code == 422
    listed = json.dumps(r.json(), ensure_ascii=False)
    assert "Second Store" in listed and "store_id" in listed


async def test_empty_day_for_a_real_store(client, seed_store, seed_employee):
    r = await client.get(URL, params={"store_id": str(seed_store.id), "date": "2026-10-15"})
    body = r.json()
    assert r.status_code == 200 and body["order_count"] == 0 and body["net_margin"] is None
    assert D(body["revenue_ex_vat"]) == D("0.00") and D(body["fixed_cost_total"]) == D("3000.00")
