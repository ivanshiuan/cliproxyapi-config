"""Pytest acceptance suite for ``restaurant_api.services.calc.discount_resolver``.

Each AC from ``specs/discount_resolver.md`` has a dedicated test function
with concrete Decimal assertions (no shape-only checks).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.discount_resolver import (
    DiscountResolveInput,
    DiscountRow,
    resolve_discounts,
)


def _row(kind: str, value: str) -> DiscountRow:
    return DiscountRow(kind=kind, value=Decimal(value))  # type: ignore[arg-type]


# ─── AC-1 ─────────────────────────────────────────────────────────────────
def test_ac01_single_percent() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[_row("percent", "0.10")],
        ),
    )
    assert out.net_revenue == Decimal("900.00")
    assert out.discount_total == Decimal("100.00")
    assert len(out.breakdown) == 1
    assert out.breakdown[0].effective_twd == Decimal("100.00")
    assert out.breakdown[0].applied_to_subtotal_after_prior == Decimal("1000.00")


# ─── AC-2 ─────────────────────────────────────────────────────────────────
def test_ac02_single_amount() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[_row("amount", "100")],
        ),
    )
    assert out.net_revenue == Decimal("900.00")
    assert out.discount_total == Decimal("100.00")


# ─── AC-3 ─────────────────────────────────────────────────────────────────
def test_ac03_single_allowance() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[_row("allowance", "50")],
        ),
    )
    assert out.net_revenue == Decimal("950.00")
    assert out.discount_total == Decimal("50.00")
    assert out.breakdown[0].kind == "allowance"


# ─── AC-4 ─────────────────────────────────────────────────────────────────
def test_ac04_single_comp() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("500"),
            discounts=[_row("comp", "0")],
        ),
    )
    assert out.net_revenue == Decimal("0.00")
    assert out.discount_total == Decimal("500.00")
    assert out.breakdown[0].effective_twd == Decimal("500.00")


# ─── AC-5 ─────────────────────────────────────────────────────────────────
def test_ac05_comp_ignores_value() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("500"),
            discounts=[_row("comp", "999")],
        ),
    )
    assert out.net_revenue == Decimal("0.00")
    # Not 999 — comp eats the running subtotal (500), not its own value.
    assert out.breakdown[0].effective_twd == Decimal("500.00")


# ─── AC-6 ─────────────────────────────────────────────────────────────────
def test_ac06_two_percents_compound() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("percent", "0.05"),
            ],
        ),
    )
    # 1000 * 0.10 = 100; running = 900
    # 900 * 0.05 = 45; running = 855
    assert out.breakdown[0].effective_twd == Decimal("100.00")
    assert out.breakdown[1].effective_twd == Decimal("45.00")
    assert out.net_revenue == Decimal("855.00")
    assert out.discount_total == Decimal("145.00")


# ─── AC-7 ─────────────────────────────────────────────────────────────────
def test_ac07_employee_equals_percent() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[_row("employee", "0.20")],
        ),
    )
    assert out.net_revenue == Decimal("800.00")
    assert out.breakdown[0].kind == "employee"
    assert out.breakdown[0].effective_twd == Decimal("200.00")

    # Mixing percent + employee == double-percent: same net regardless.
    mixed = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("employee", "0.10"),
            ],
        ),
    )
    double_pct = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("percent", "0.10"),
            ],
        ),
    )
    assert mixed.net_revenue == double_pct.net_revenue


# ─── AC-8 ─────────────────────────────────────────────────────────────────
def test_ac08_stacked_percent_plus_amount() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("amount", "50"),
            ],
        ),
    )
    # 1000 -> 900 (after percent) -> 850 (after amount)
    assert out.net_revenue == Decimal("850.00")
    assert out.breakdown[0].kind == "percent"
    assert out.breakdown[0].effective_twd == Decimal("100.00")
    assert out.breakdown[1].kind == "amount"
    assert out.breakdown[1].effective_twd == Decimal("50.00")


# ─── AC-9 ─────────────────────────────────────────────────────────────────
def test_ac09_stacked_percent_plus_amount_plus_comp() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("amount", "50"),
                _row("comp", "0"),
            ],
        ),
    )
    assert out.net_revenue == Decimal("0.00")
    assert out.discount_total == Decimal("1000.00")
    assert out.breakdown[0].effective_twd == Decimal("100.00")
    assert out.breakdown[1].effective_twd == Decimal("50.00")
    assert out.breakdown[2].effective_twd == Decimal("850.00")


# ─── AC-10 ────────────────────────────────────────────────────────────────
def test_ac10_comp_ordering_invariance() -> None:
    a = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("comp", "0"),
                _row("percent", "0.10"),
            ],
        ),
    )
    b = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("percent", "0.10"),
                _row("comp", "0"),
            ],
        ),
    )
    assert a.net_revenue == Decimal("0.00")
    assert b.net_revenue == Decimal("0.00")
    assert a.discount_total == Decimal("1000.00")
    assert b.discount_total == Decimal("1000.00")
    # Breakdown order follows input order:
    assert [d.kind for d in a.breakdown] == ["comp", "percent"]
    assert [d.kind for d in b.breakdown] == ["percent", "comp"]


# ─── AC-11 ────────────────────────────────────────────────────────────────
def test_ac11_negative_net_revenue_allowed() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[_row("amount", "1500")],
        ),
    )
    assert out.net_revenue == Decimal("-500.00")
    assert out.discount_total == Decimal("1500.00")
    assert out.breakdown[0].effective_twd == Decimal("1500.00")
    assert out.breakdown[0].applied_to_subtotal_after_prior == Decimal("1000.00")


# ─── AC-12 ────────────────────────────────────────────────────────────────
def test_ac12_two_amounts_both_apply() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                _row("amount", "200"),
                _row("amount", "300"),
            ],
        ),
    )
    assert out.net_revenue == Decimal("500.00")
    assert out.discount_total == Decimal("500.00")
    assert out.breakdown[0].effective_twd == Decimal("200.00")
    assert out.breakdown[1].effective_twd == Decimal("300.00")


# ─── AC-13 ────────────────────────────────────────────────────────────────
def test_ac13_decimal_precision_compound_rounding() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("99.99"),
            discounts=[_row("percent", "0.07")],
        ),
    )
    # 99.99 * 0.07 = 6.9993 → ROUND_HALF_UP → 7.00
    assert out.breakdown[0].effective_twd == Decimal("7.00")
    # 99.99 - 6.9993 = 92.9907 → ROUND_HALF_UP at 2dp → 92.99
    assert out.net_revenue == Decimal("92.99")


# ─── AC-14 ────────────────────────────────────────────────────────────────
def test_ac14_empty_discounts() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[],
        ),
    )
    assert out.net_revenue == Decimal("1000.00")
    assert out.discount_total == Decimal("0.00")
    assert out.breakdown == []


# ─── AC-15 ────────────────────────────────────────────────────────────────
def test_ac15_float_input_rejected() -> None:
    with pytest.raises(ValidationError):
        DiscountRow(kind="percent", value=0.1)  # type: ignore[arg-type]
    # Strings work.
    r = DiscountRow(kind="percent", value=Decimal("0.10"))
    assert r.value == Decimal("0.10")


# ─── AC-16 ────────────────────────────────────────────────────────────────
def test_ac16_percent_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        DiscountRow(kind="percent", value=Decimal("1.5"))
    with pytest.raises(ValidationError):
        DiscountRow(kind="percent", value=Decimal("-0.1"))
    with pytest.raises(ValidationError):
        DiscountRow(kind="employee", value=Decimal("1.01"))


# ─── AC-17 ────────────────────────────────────────────────────────────────
def test_ac17_multiple_comps() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("500"),
            discounts=[
                _row("comp", "0"),
                _row("comp", "0"),
            ],
        ),
    )
    assert out.net_revenue == Decimal("0.00")
    assert out.discount_total == Decimal("500.00")
    assert out.breakdown[0].effective_twd == Decimal("500.00")
    assert out.breakdown[1].effective_twd == Decimal("0.00")


# ─── Extra edge: subtotal == 0 ────────────────────────────────────────────
def test_zero_subtotal_no_effect() -> None:
    out = resolve_discounts(
        DiscountResolveInput(
            subtotal=Decimal("0"),
            discounts=[
                _row("percent", "0.10"),
                _row("amount", "100"),
                _row("comp", "0"),
            ],
        ),
    )
    # 0 * 0.10 = 0; running stays 0; amount drops to -100; comp finds <=0
    # so effective_twd=0, running stays -100.
    assert out.breakdown[0].effective_twd == Decimal("0.00")
    assert out.breakdown[1].effective_twd == Decimal("100.00")
    assert out.breakdown[2].effective_twd == Decimal("0.00")
    assert out.net_revenue == Decimal("-100.00")
