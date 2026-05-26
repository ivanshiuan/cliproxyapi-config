"""Smoke tests for the restaurant_api FastAPI scaffold.

Verifies the app boots, routes are wired, and metadata is well-formed.
DB-dependent endpoints are exercised with a mocked DB ping so we don't need
a live Postgres in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from restaurant_api.main import app


def test_version_endpoint():
    client = TestClient(app)
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"]
    assert body["name"]


def test_root_endpoint():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "restaurant_api"


def test_health_endpoint_db_ok():
    with patch(
        "restaurant_api.main.ping_db",
        AsyncMock(return_value={"ok": True, "version": "PostgreSQL 16", "database": "resto_test"}),
    ):
        client = TestClient(app)
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True


def test_health_endpoint_db_down():
    with patch(
        "restaurant_api.main.ping_db",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        client = TestClient(app)
        resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False


def test_models_metadata_has_18_tables():
    from restaurant_api.models import Base

    assert len(Base.metadata.tables) == 18

    expected = {
        "tenants",
        "stores",
        "employees",
        "menu_categories",
        "menu_items",
        "ingredients",
        "recipes",
        "stock_movements",
        "orders",
        "order_lines",
        "order_discounts",
        "order_payments",
        "waste_events",
        "staff_meal_events",
        "tasting_events",
        "shifts",
        "time_clocks",
        "leave_requests",
    }
    actual = set(Base.metadata.tables.keys())
    assert actual == expected, f"missing: {expected - actual}; extra: {actual - expected}"


def test_money_columns_are_numeric_14_4():
    """Spot-check: revenue-critical columns must use the Money type (Numeric(14,4))."""
    from sqlalchemy import Numeric

    from restaurant_api.models import MenuItem, OrderLine, WasteEvent

    def is_money(col):
        t = col.type
        return isinstance(t, Numeric) and t.precision == 14 and t.scale == 4

    assert is_money(MenuItem.__table__.c.price)
    assert is_money(OrderLine.__table__.c.unit_price)
    assert is_money(WasteEvent.__table__.c.cost)


def test_every_business_table_has_tenant_id():
    """tenant_id present on every table except tenants itself."""
    from restaurant_api.models import Base

    for name, table in Base.metadata.tables.items():
        if name == "tenants":
            continue
        assert "tenant_id" in table.c, f"{name} missing tenant_id"
