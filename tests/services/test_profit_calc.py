"""Pytest acceptance suite for ``restaurant_api.services.calc.profit_calc``.

Each AC from ``specs/profit_calc.md`` has a dedicated test function with
concrete Decimal assertions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.profit_calc import (
    CostEvent,
    DailyPnLInput,
    Discount,
    FixedCostLine,
    OrderLine,
    PlatformFee,
    compute_daily_pnl,
)

D = Decimal
_TODAY = date(2026, 6, 4)
_STORE = "store-a"


def _line(
    qty: str,
    unit_price: str,
    cogs_actual: str,
    cogs_theoretical: str,
    item_id: str = "x",
) -> OrderLine:
    return OrderLine(
        item_id=item_id,
        qty=D(qty),
        unit_price=D(unit_price),
        cogs_actual=D(cogs_actual),
        cogs_theoretical=D(cogs_theoretical),
    )


def _empty_input(
    orders: list[OrderLine] | None = None,
    discounts: list[Discount] | None = None,
    cost_events: list[CostEvent] | None = None,
    platform_fees: list[PlatformFee] | None = None,
    fixed_costs: list[FixedCostLine] | None = None,
    labor_cost: Decimal = Decimal("0"),
) -> DailyPnLInput:
    return DailyPnLInput(
        business_date=_TODAY,
        store_id=_STORE,
        orders=orders or [],
        discounts=discounts or [],
        cost_events=cost_events or [],
        platform_fees=platform_fees or [],
        fixed_costs=fixed_costs or [],
        labor_cost=labor_cost,
    )


# ─── AC-1 ─────────────────────────────────────────────────────────────────
def test_ac01_happy_path() -> None:
    # 10 lines * 1000 = 10,000 gross
    orders = [_line("1", "1000", "300", "300", item_id=f"i{i}") for i in range(10)]
    out = compute_daily_pnl(
        _empty_input(
            orders=orders,
            cost_events=[CostEvent(kind="waste", cost=D("200"))],
            fixed_costs=[FixedCostLine(name="rent", daily_amount=D("2000"))],
            labor_cost=D("1500"),
        ),
    )
    assert out.gross_revenue == D("10000.00")
    assert out.discount_total == D("0.00")
    assert out.net_revenue == D("10000.00")
    assert out.cogs_actual_total == D("3000.00")
    assert out.cogs_theoretical_total == D("3000.00")
    # gross_profit_real = 10000 - 3000 - 200 - 0 - 0 = 6800
    assert out.gross_profit_real == D("6800.00")
    # net = 6800 - 0 - 1500 - 2000 = 3300
    assert out.net_profit_real == D("3300.00")
    assert out.cogs_variance_flag is False


# ─── AC-2 ─────────────────────────────────────────────────────────────────
def test_ac02_percent_discount() -> None:
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1000", "0", "0")],
            discounts=[Discount(order_id="o1", kind="percent", value=D("0.10"))],
        ),
    )
    assert out.discount_total == D("100.00")
    assert out.net_revenue == D("900.00")


# ─── AC-3 ─────────────────────────────────────────────────────────────────
def test_ac03_amount_discount() -> None:
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1000", "0", "0")],
            discounts=[Discount(order_id="o1", kind="amount", value=D("100"))],
        ),
    )
    assert out.discount_total == D("100.00")
    assert out.net_revenue == D("900.00")


# ─── AC-4 ─────────────────────────────────────────────────────────────────
def test_ac04_comp_whole_order() -> None:
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "500", "0", "0")],
            discounts=[Discount(order_id="o1", kind="comp", value=D("0"))],
        ),
    )
    assert out.discount_total == D("500.00")
    assert out.net_revenue == D("0.00")


# ─── AC-5 ─────────────────────────────────────────────────────────────────
def test_ac05_cogs_variance_flag_fires() -> None:
    # 1 line @ 10000, actual=4000, theoretical=3000 → variance=0.1000
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "10000", "4000", "3000")],
        ),
    )
    assert out.net_revenue == D("10000.00")
    assert out.cogs_variance_pct == D("0.1000")
    assert out.cogs_variance_flag is True


# ─── AC-6 ─────────────────────────────────────────────────────────────────
def test_ac06_threshold_suppression() -> None:
    # actual=3490, theoretical=3000, net=10000 → 0.049 < 0.05 → flag False
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "10000", "3490", "3000")],
        ),
    )
    assert out.cogs_variance_pct == D("0.0490")
    assert out.cogs_variance_flag is False


# ─── AC-7 ─────────────────────────────────────────────────────────────────
def test_ac07_zero_net_revenue_no_div_by_zero() -> None:
    out = compute_daily_pnl(_empty_input())
    assert out.gross_revenue == D("0.00")
    assert out.discount_total == D("0.00")
    assert out.net_revenue == D("0.00")
    assert out.cogs_variance_pct == D("0")
    assert out.cogs_variance_flag is False


# ─── AC-8 ─────────────────────────────────────────────────────────────────
def test_ac08_decimal_precision_2dp() -> None:
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1.005", "0.005", "0.005")],
        ),
    )
    # 1 * 1.005 = 1.005 → ROUND_HALF_UP at 2dp → 1.01
    assert out.gross_revenue == D("1.01")
    assert out.net_revenue == D("1.01")
    assert out.cogs_actual_total == D("0.01")
    # Every money output is a Decimal with exponent -2 (i.e. 2dp).
    for value in (
        out.gross_revenue,
        out.discount_total,
        out.net_revenue,
        out.cogs_actual_total,
        out.cogs_theoretical_total,
        out.cost_waste,
        out.cost_staff_meal,
        out.cost_tasting,
        out.platform_fee_total,
        out.labor_cost,
        out.fixed_cost_total,
        out.gross_profit_real,
        out.net_profit_real,
    ):
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -2, f"{value!r} not at 2dp"


# ─── AC-9 ─────────────────────────────────────────────────────────────────
def test_ac09_negative_qty_rejected() -> None:
    with pytest.raises(ValidationError):
        OrderLine(
            item_id="x",
            qty=D("-1"),
            unit_price=D("100"),
            cogs_actual=D("0"),
            cogs_theoretical=D("0"),
        )


# ─── AC-10 ────────────────────────────────────────────────────────────────
def test_ac10_float_input_rejected_string_accepted() -> None:
    with pytest.raises(ValidationError):
        OrderLine(
            item_id="x",
            qty=D("1"),
            unit_price=1000.0,  # type: ignore[arg-type]
            cogs_actual=D("0"),
            cogs_theoretical=D("0"),
        )
    # String coercion works.
    ol = OrderLine(
        item_id="x",
        qty=D("1"),
        unit_price=D("1000.00"),
        cogs_actual=D("0"),
        cogs_theoretical=D("0"),
    )
    assert ol.unit_price == D("1000.00")


# ─── AC-11 ────────────────────────────────────────────────────────────────
def test_ac11_stacked_discounts_per_order() -> None:
    # subtotal=1000; percent(0.10) → 900; amount(50) → 850; comp → 0.
    # discount_total should equal full gross_revenue = 1000.
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1000", "0", "0")],
            discounts=[
                Discount(order_id="o1", kind="percent", value=D("0.10")),
                Discount(order_id="o1", kind="amount", value=D("50")),
                Discount(order_id="o1", kind="comp", value=D("0")),
            ],
        ),
    )
    assert out.net_revenue == D("0.00")
    assert out.discount_total == D("1000.00")


# ─── AC-12 ────────────────────────────────────────────────────────────────
def test_ac12_platform_fee_below_gross_line() -> None:
    # net_revenue=1000, platform_fee=100, cogs_actual=300.
    # gross_profit_real = 1000 - 300 - 0 - 0 - 0 = 700 (no platform_fee in gross)
    # net_profit_real = 700 - 100 - 0 - 0 = 600
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1000", "300", "0")],
            platform_fees=[PlatformFee(source="ubereats", amount=D("100"))],
        ),
    )
    assert out.gross_profit_real == D("700.00")
    assert out.net_profit_real == D("600.00")


# ─── AC-13 ────────────────────────────────────────────────────────────────
def test_ac13_negative_net_revenue_allowed() -> None:
    # discount > gross → net_revenue must go negative, not clamp.
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "1000", "0", "0")],
            discounts=[Discount(order_id="o1", kind="amount", value=D("1500"))],
        ),
    )
    assert out.net_revenue == D("-500.00")
    assert out.discount_total == D("1500.00")
    # downstream calc proceeds:
    assert out.gross_profit_real == D("-500.00")


# ─── AC-14 ────────────────────────────────────────────────────────────────
def test_ac14_zero_theoretical_no_div_zero() -> None:
    # cogs_actual>0, cogs_theoretical=0, net_revenue>0:
    # variance_pct = (actual - 0) / net = 600 / 10000 = 0.06 → flag True.
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "10000", "600", "0")],
        ),
    )
    assert out.cogs_theoretical_total == D("0.00")
    assert out.cogs_variance_pct == D("0.0600")
    assert out.cogs_variance_flag is True


# ─── AC-15 ────────────────────────────────────────────────────────────────
def test_ac15_cost_event_aggregation() -> None:
    events = [
        CostEvent(kind="waste", cost=D("100")),
        CostEvent(kind="waste", cost=D("100")),
        CostEvent(kind="waste", cost=D("100")),
        CostEvent(kind="staff_meal", cost=D("50")),
        CostEvent(kind="staff_meal", cost=D("50")),
        CostEvent(kind="tasting", cost=D("200")),
    ]
    out = compute_daily_pnl(_empty_input(cost_events=events))
    assert out.cost_waste == D("300.00")
    assert out.cost_staff_meal == D("100.00")
    assert out.cost_tasting == D("200.00")


# ─── Edge: variance exactly at threshold ──────────────────────────────────
def test_variance_at_exact_threshold_is_strictly_greater() -> None:
    # actual=3500, theoretical=3000, net=10000 → variance = 0.05 exactly.
    # Spec says strictly `>`, so flag must be False.
    out = compute_daily_pnl(
        _empty_input(
            orders=[_line("1", "10000", "3500", "3000")],
        ),
    )
    assert out.cogs_variance_pct == D("0.0500")
    assert out.cogs_variance_flag is False
