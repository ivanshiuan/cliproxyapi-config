"""Pytest acceptance suite for ``restaurant_api.services.calc.bom_consumer``.

Each AC from ``specs/bom_consumer.md`` has a dedicated test function.
Concrete Decimal values are asserted everywhere — no shape-only assertions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.bom_consumer import (
    BOMConsumeInput,
    MissingRecipeError,
    RecipeRow,
    consume_bom,
)


def _mk(
    ingredient_id: str,
    qty_per_serving: str,
    standard_unit_cost: str,
) -> RecipeRow:
    return RecipeRow(
        ingredient_id=ingredient_id,
        qty_per_serving=Decimal(qty_per_serving),
        standard_unit_cost=Decimal(standard_unit_cost),
    )


# ─── AC-1 ─────────────────────────────────────────────────────────────────
def test_ac01_single_ingredient_happy_path() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m1",
            qty_sold=Decimal("1"),
            recipes=[_mk("ing-a", "100", "0.5")],
        ),
    )
    assert len(out.movements) == 1
    assert out.movements[0].ingredient_id == "ing-a"
    assert out.movements[0].qty == Decimal("-100")
    assert out.movements[0].unit_cost == Decimal("0.5")
    assert out.theoretical_cogs == Decimal("50.00")


# ─── AC-2 ─────────────────────────────────────────────────────────────────
def test_ac02_multi_ingredient_recipe() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m2",
            qty_sold=Decimal("1"),
            recipes=[
                _mk("a", "100", "0.5"),
                _mk("b", "50", "1.0"),
                _mk("c", "5", "2.0"),
            ],
        ),
    )
    assert [m.ingredient_id for m in out.movements] == ["a", "b", "c"]
    assert out.movements[0].qty == Decimal("-100")
    assert out.movements[1].qty == Decimal("-50")
    assert out.movements[2].qty == Decimal("-5")
    assert out.theoretical_cogs == Decimal("110.00")


# ─── AC-3 ─────────────────────────────────────────────────────────────────
def test_ac03_qty_multiplier() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m3",
            qty_sold=Decimal("3"),
            recipes=[
                _mk("a", "100", "0.5"),
                _mk("b", "50", "1.0"),
                _mk("c", "5", "2.0"),
            ],
        ),
    )
    assert out.movements[0].qty == Decimal("-300")
    assert out.movements[1].qty == Decimal("-150")
    assert out.movements[2].qty == Decimal("-15")
    assert out.theoretical_cogs == Decimal("330.00")


# ─── AC-4 ─────────────────────────────────────────────────────────────────
def test_ac04_fractional_qty_sold() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m4",
            qty_sold=Decimal("0.5"),
            recipes=[_mk("a", "100", "1")],
        ),
    )
    assert out.movements[0].qty == Decimal("-50.0")
    assert out.theoretical_cogs == Decimal("50.00")


# ─── AC-5 ─────────────────────────────────────────────────────────────────
def test_ac05_missing_recipe_raises() -> None:
    with pytest.raises(MissingRecipeError):
        consume_bom(
            BOMConsumeInput(
                menu_item_id="m5",
                qty_sold=Decimal("1"),
                recipes=[],
            ),
        )


# ─── AC-6 ─────────────────────────────────────────────────────────────────
def test_ac06_zero_qty_sold_rejected() -> None:
    with pytest.raises(ValidationError):
        BOMConsumeInput(
            menu_item_id="m6",
            qty_sold=Decimal("0"),
            recipes=[_mk("a", "1", "1")],
        )


# ─── AC-7 ─────────────────────────────────────────────────────────────────
def test_ac07_negative_qty_sold_rejected() -> None:
    with pytest.raises(ValidationError):
        BOMConsumeInput(
            menu_item_id="m7",
            qty_sold=Decimal("-1"),
            recipes=[_mk("a", "1", "1")],
        )


# ─── AC-8 ─────────────────────────────────────────────────────────────────
def test_ac08_zero_standard_unit_cost() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m8",
            qty_sold=Decimal("2"),
            recipes=[
                _mk("a", "10", "0"),
                _mk("b", "5", "3"),
            ],
        ),
    )
    assert out.movements[0].qty == Decimal("-20")
    assert out.movements[0].unit_cost == Decimal("0")
    assert out.movements[1].qty == Decimal("-10")
    # Only ingredient b contributes (5 * 2 * 3 = 30).
    assert out.theoretical_cogs == Decimal("30.00")


# ─── AC-9 ─────────────────────────────────────────────────────────────────
def test_ac09_decimal_precision_high() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m9",
            qty_sold=Decimal("7"),
            recipes=[_mk("spice", "0.0125", "80.0000")],
        ),
    )
    # 0.0125 * 7 = 0.0875, NOT 2dp quantized.
    assert out.movements[0].qty == Decimal("-0.0875")
    assert out.movements[0].unit_cost == Decimal("80.0000")
    # 0.0875 * 80 = 7.00 → quantize keeps 7.00.
    assert out.theoretical_cogs == Decimal("7.00")


# ─── AC-10 ────────────────────────────────────────────────────────────────
def test_ac10_theoretical_cogs_quantized_to_2dp() -> None:
    # 0.789 * 1 = 0.789 → quantize ROUND_HALF_UP → 0.79
    # 12.345 * 10 = 123.45 → quantize → 123.45 (already 2dp)
    # Force a 3rd-decimal rounding: 0.005 * 1 unit @ qty 1 → 0.005 cogs → 0.01
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m10",
            qty_sold=Decimal("1"),
            recipes=[
                _mk("a", "1", "123.456"),  # 1 * 123.456 = 123.456 → 123.46
            ],
        ),
    )
    assert out.theoretical_cogs == Decimal("123.46")


# ─── AC-11 ────────────────────────────────────────────────────────────────
def test_ac11_float_input_rejected_strict() -> None:
    with pytest.raises(ValidationError):
        RecipeRow(
            ingredient_id="a",
            qty_per_serving=0.5,  # type: ignore[arg-type]
            standard_unit_cost=Decimal("1"),
        )
    # String form works fine.
    row = RecipeRow(
        ingredient_id="a",
        qty_per_serving=Decimal("0.5"),
        standard_unit_cost=Decimal("1"),
    )
    assert row.qty_per_serving == Decimal("0.5")


# ─── AC-12 ────────────────────────────────────────────────────────────────
def test_ac12_output_immutability() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m12",
            qty_sold=Decimal("1"),
            recipes=[_mk("a", "1", "1")],
        ),
    )
    # frozen=True on StockMovementDelta blocks attribute reassignment.
    with pytest.raises(ValidationError):
        out.movements[0].qty = Decimal("0")  # type: ignore[misc]
    # frozen=True on BOMConsumeOutput blocks reassigning the list attr.
    with pytest.raises(ValidationError):
        out.movements = []  # type: ignore[misc]


# ─── AC-13 ────────────────────────────────────────────────────────────────
def test_ac13_movements_order_preserved() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m13",
            qty_sold=Decimal("1"),
            recipes=[
                _mk("z-last", "1", "1"),
                _mk("a-first", "1", "1"),
                _mk("m-mid", "1", "1"),
            ],
        ),
    )
    assert [m.ingredient_id for m in out.movements] == [
        "z-last",
        "a-first",
        "m-mid",
    ]


# ─── AC-14 ────────────────────────────────────────────────────────────────
def test_ac14_movement_sign_is_negative() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m14",
            qty_sold=Decimal("2"),
            recipes=[
                _mk("a", "100", "1"),
                _mk("b", "50", "1"),
                _mk("c", "5", "1"),
            ],
        ),
    )
    for delta in out.movements:
        assert delta.qty < Decimal("0"), f"qty must be negative, got {delta.qty}"


# ─── AC-15 ────────────────────────────────────────────────────────────────
def test_ac15_movement_type_literal() -> None:
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="m15",
            qty_sold=Decimal("1"),
            recipes=[
                _mk("a", "1", "1"),
                _mk("b", "1", "1"),
            ],
        ),
    )
    for delta in out.movements:
        assert delta.movement_type == "sale_consume"


# ─── Realistic worked-example: 義大利肉醬麵 ─────────────────────────────
def test_pasta_bolognese_worked_example() -> None:
    """180g pasta + 120g beef + 80g sauce + 5g spice — a sanity check."""
    out = consume_bom(
        BOMConsumeInput(
            menu_item_id="pasta-bolognese",
            qty_sold=Decimal("2"),
            recipes=[
                _mk("pasta", "180", "0.1"),  # 360g * 0.1 = 36
                _mk("beef", "120", "0.4"),  # 240g * 0.4 = 96
                _mk("sauce", "80", "0.2"),  # 160g * 0.2 = 32
                _mk("spice", "5", "1.5"),  # 10g * 1.5 = 15
            ],
        ),
    )
    assert out.movements[0].qty == Decimal("-360")
    assert out.movements[1].qty == Decimal("-240")
    assert out.movements[2].qty == Decimal("-160")
    assert out.movements[3].qty == Decimal("-10")
    # total = 36 + 96 + 32 + 15 = 179
    assert out.theoretical_cogs == Decimal("179.00")
