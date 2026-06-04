"""BOM consumer — pure function that expands a sold menu item into stock deltas.

Contract: ``specs/bom_consumer.md``.

Given ``(menu_item_id, qty_sold, recipes)`` this module emits the
``StockMovementDelta`` rows the caller must append to the ledger plus the
theoretical COGS for the order line. The module is intentionally ignorant of
the DB, of recipe-version selection (caller already resolved the active rows)
and of multi-tenant scoping.

Design notes:
- We reject ``float`` on every Decimal field via ``StrictDecimal`` (a
  ``BeforeValidator``-annotated alias) rather than ``ConfigDict(strict=True)``,
  to stay consistent with the rest of the codebase and not break JSON-string
  Decimal coercion (see ``restaurant_api/schemas/orders.py``).
- ``qty`` and ``unit_cost`` are NOT quantized to 2dp — they must stay at
  ``Numeric(14, 4)`` precision so 0.0125 g spices don't get rounded away.
- Only ``theoretical_cogs`` (a TWD figure) is quantized to 2dp on output.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

MOVEMENT_TYPE_SALE_CONSUME = "sale_consume"
_TWD_QUANTUM = Decimal("0.01")


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on Decimal fields.

    We want JSON strings like ``"0.5"`` to coerce to Decimal, but bare
    Python floats carry binary rounding errors and must never be accepted.
    """
    if isinstance(value, float):
        raise ValueError("float literals are not accepted; use a string or Decimal")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class MissingRecipeError(ValueError):
    """Raised when ``consume_bom`` is called with an empty ``recipes`` list.

    We fail loudly because silent zero-cogs from a missing recipe is the
    #1 cause of "my P&L is wrong" tickets in real restaurants.
    """


class RecipeRow(BaseModel):
    """One active BOM row for a menu item.

    Mirrors ``restaurant_api.models.inventory.Recipe`` joined with the
    ingredient's ``standard_cost_per_unit``.
    """

    model_config = ConfigDict(frozen=True)

    ingredient_id: str = Field(min_length=1)
    qty_per_serving: StrictDecimal = Field(gt=Decimal("0"))
    standard_unit_cost: StrictDecimal = Field(ge=Decimal("0"))


class BOMConsumeInput(BaseModel):
    """Inputs for one order line's BOM expansion."""

    model_config = ConfigDict(frozen=True)

    menu_item_id: str = Field(min_length=1)
    qty_sold: StrictDecimal = Field(gt=Decimal("0"))
    recipes: list[RecipeRow]


class StockMovementDelta(BaseModel):
    """One ledger row delta to be INSERTed by the caller.

    Aligned with ``restaurant_api.models.inventory.StockMovement``:
    ``qty`` is SIGNED (always negative here, because a sale removes stock).
    """

    model_config = ConfigDict(frozen=True)

    ingredient_id: str
    qty: Decimal
    unit_cost: Decimal
    movement_type: str = MOVEMENT_TYPE_SALE_CONSUME


class BOMConsumeOutput(BaseModel):
    """Result of one ``consume_bom`` call."""

    model_config = ConfigDict(frozen=True)

    movements: list[StockMovementDelta]
    theoretical_cogs: Decimal


def _quantize_twd(value: Decimal) -> Decimal:
    """Quantize a TWD amount to 2dp using bankers-friendly HALF_UP rounding."""
    return value.quantize(_TWD_QUANTUM, rounding=ROUND_HALF_UP)


def consume_bom(input: BOMConsumeInput) -> BOMConsumeOutput:
    """Compute stock-movement deltas for one sold order line.

    Pure function: no I/O, no globals, no logging. Output ``movements``
    preserve the input recipe order so the caller can round-trip-compare
    each delta against its source row.

    Example::

        out = consume_bom(BOMConsumeInput(
            menu_item_id="pasta-bolognese",
            qty_sold=Decimal("2"),
            recipes=[
                RecipeRow(
                    ingredient_id="pasta",
                    qty_per_serving=Decimal("180"),
                    standard_unit_cost=Decimal("0.5"),
                ),
            ],
        ))
        # out.movements[0].qty == Decimal("-360")
        # out.theoretical_cogs == Decimal("180.00")

    Raises:
        pydantic.ValidationError: malformed input (float, non-positive qty, ...).
        MissingRecipeError: ``input.recipes`` is empty.
    """
    if not input.recipes:
        raise MissingRecipeError(
            f"no active recipes for menu_item_id={input.menu_item_id!r}; "
            "fix recipe data upstream",
        )

    movements: list[StockMovementDelta] = []
    cogs_acc = Decimal("0")
    for row in input.recipes:
        deducted_qty = row.qty_per_serving * input.qty_sold
        signed_qty = -deducted_qty
        movements.append(
            StockMovementDelta(
                ingredient_id=row.ingredient_id,
                qty=signed_qty,
                unit_cost=row.standard_unit_cost,
                movement_type=MOVEMENT_TYPE_SALE_CONSUME,
            ),
        )
        cogs_acc += deducted_qty * row.standard_unit_cost

    return BOMConsumeOutput(
        movements=movements,
        theoretical_cogs=_quantize_twd(cogs_acc),
    )


__all__ = [
    "MOVEMENT_TYPE_SALE_CONSUME",
    "BOMConsumeInput",
    "BOMConsumeOutput",
    "MissingRecipeError",
    "RecipeRow",
    "StockMovementDelta",
    "consume_bom",
]
