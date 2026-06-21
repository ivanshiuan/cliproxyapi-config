"""Smoke tests for the restaurant_api FastAPI scaffold.

Verifies the app boots, routes are wired, and metadata is well-formed.
DB-dependent endpoints are exercised with a mocked DB ping so we don't need
a live Postgres in CI.
"""

from __future__ import annotations

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


# Health endpoints moved to /health/live + /health/ready; see tests/test_health_endpoints.py
# for the live/ready/composite coverage. The legacy `/health` is now an alias
# to /health/ready and tested there.


def test_models_metadata_has_30_tables():
    """18 core + 5 closed-loop + 2 (reservations + walk_in_queue) + 1 national
    + 4 marketing-campaign (wheel-spin lottery) + 1 stored-value + 1 referral
    + 1 UGC = 33 total."""
    from restaurant_api.models import Base

    expected = {
        # Phase 1 core (18)
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
        # Phase 1 closed-loop (5)
        "audit_log",
        "cash_drawer_sessions",
        "customers",
        "customer_points_ledger",
        "embeddings",
        # Reservation + queue (2)
        "reservations",
        "walk_in_queue",
        # National-scope reference data (1) — not tenant-scoped
        "public_holidays",
        # Marketing campaigns — wheel-spin lottery (4)
        "marketing_campaigns",
        "campaign_prizes",
        "campaign_spins",
        "campaign_vouchers",
        # Stored-value (prepaid wallet) (1)
        "customer_stored_value_ledger",
        # Referral flywheel (1)
        "referrals",
        # UGC (打卡 / 評論換獎) (1)
        "ugc_submissions",
    }
    actual = set(Base.metadata.tables.keys())
    assert actual == expected, f"missing: {expected - actual}; extra: {actual - expected}"
    assert len(Base.metadata.tables) == 33


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
    """tenant_id present on every BUSINESS table.

    Excluded by design:
      - ``tenants`` itself (root of the tenancy graph)
      - ``public_holidays`` (national reference data — same calendar for
        every tenant in a given country)
    """
    from restaurant_api.models import Base

    national_scope = {"public_holidays"}
    for name, table in Base.metadata.tables.items():
        if name == "tenants" or name in national_scope:
            continue
        assert "tenant_id" in table.c, f"{name} missing tenant_id"


def test_invoice_status_enum_covers_taiwan_lifecycle():
    """Smoke check: InvoiceStatus must include 6 specific lifecycle values."""
    from restaurant_api.models import InvoiceStatus

    required = {"pending", "issued", "voided", "allowance", "winner", "redeemed"}
    assert {v.value for v in InvoiceStatus} == required


def test_customer_tier_enum_has_four_tiers():
    from restaurant_api.models import CustomerTier

    assert {v.value for v in CustomerTier} == {"regular", "silver", "gold", "platinum"}


def test_menu_item_allergens_jsonb_present():
    from restaurant_api.models import MenuItem

    assert "allergens" in MenuItem.__table__.c
    col = MenuItem.__table__.c.allergens
    # JSONB at the DB level
    assert col.type.__class__.__name__ == "JSONB"


def test_ingredient_has_lot_no_and_expiry_date():
    from restaurant_api.models import Ingredient

    assert "lot_no" in Ingredient.__table__.c
    assert "expiry_date" in Ingredient.__table__.c


def test_stock_movement_has_lot_no():
    """Food-safety traceability: the ledger row records the consumed lot."""
    from restaurant_api.models import StockMovement

    assert "lot_no" in StockMovement.__table__.c


def test_kitchen_station_and_status_enums():
    """KDS routing + lifecycle enums must cover the 4 stations and 5 states."""
    from restaurant_api.models import KitchenStation, KitchenStatus

    assert {v.value for v in KitchenStation} == {"kitchen", "bar", "dessert", "counter"}
    assert {v.value for v in KitchenStatus} == {
        "queued",
        "cooking",
        "ready",
        "served",
        "cancelled",
    }


def test_order_line_has_kds_fields():
    from restaurant_api.models import OrderLine

    cols = OrderLine.__table__.c
    for f in (
        "kitchen_station",
        "kitchen_status",
        "sent_to_kitchen_at",
        "cooking_started_at",
        "ready_at",
        "served_at",
    ):
        assert f in cols, f"OrderLine missing KDS field: {f}"


def test_reservation_lifecycle_enum():
    from restaurant_api.models import ReservationStatus

    assert {v.value for v in ReservationStatus} == {
        "booked",
        "confirmed",
        "seated",
        "completed",
        "no_show",
        "cancelled",
    }


def test_walk_in_queue_lifecycle_enum():
    from restaurant_api.models import QueueStatus

    assert {v.value for v in QueueStatus} == {"waiting", "called", "seated", "abandoned"}
