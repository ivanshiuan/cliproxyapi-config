"""Discount resolver — pure function that resolves a stack of order discounts.

Contract: ``specs/discount_resolver.md``.

The Taiwan F&B world has no industry-standard stacking order; we pin one
here so every upstream / downstream module agrees on the math:

    1. ``percent``    — compounding ratio on the running subtotal
    2. ``employee``   — same arithmetic as ``percent`` (0..1 ratio)
    3. ``amount``     — flat TWD off the running subtotal
    4. ``allowance``  — flat TWD off the running subtotal
    5. ``comp``       — zeroes the running subtotal (boss "comps" the table)

``breakdown`` is emitted in INPUT order (caller writes back to
``order_discounts`` rows without re-sorting) but each ``effective_twd`` is
computed in the canonical step order above. ``net_revenue`` is allowed to go
negative — restaurants sometimes back-date an allowance that exceeds the
original ticket, and clamping to zero would hide that loss from the P&L.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

DiscountKind = Literal["percent", "amount", "comp", "allowance", "employee"]

# Canonical stacking order — module-level so callers can audit/reuse.
STACKING_ORDER: tuple[DiscountKind, ...] = (
    "percent",
    "employee",
    "amount",
    "allowance",
    "comp",
)

_TWD_QUANTUM = Decimal("0.01")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on Decimal fields."""
    if isinstance(value, float):
        raise ValueError("float literals are not accepted; use a string or Decimal")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class DiscountRow(BaseModel):
    """One row from ``order_discounts``, projected to the fields we use.

    ``value`` semantics depends on ``kind``:
    - ``percent`` / ``employee``  → ratio in [0, 1] (e.g. 0.10 = 10% off)
    - ``amount`` / ``allowance``  → TWD >= 0
    - ``comp``                    → informational only; comp always
      zeroes the running subtotal regardless of value.
    """

    model_config = ConfigDict(frozen=True)

    kind: DiscountKind
    value: StrictDecimal

    @model_validator(mode="after")
    def _check_value_range(self) -> DiscountRow:
        if self.kind in ("percent", "employee"):
            if self.value < _ZERO or self.value > _ONE:
                raise ValueError(
                    f"{self.kind} discount value must be between 0 and 1 (got {self.value})",
                )
        else:
            if self.value < _ZERO:
                raise ValueError(f"{self.kind} discount value must be >= 0 (got {self.value})")
        return self


class DiscountResolveInput(BaseModel):
    """Input for ``resolve_discounts``."""

    model_config = ConfigDict(frozen=True)

    subtotal: StrictDecimal = Field(ge=_ZERO)
    discounts: list[DiscountRow]


class ResolvedDiscount(BaseModel):
    """One discount after the stacking-order has been applied.

    ``original_value`` echoes the input ratio/amount untouched.
    ``effective_twd`` is the TWD this discount actually removed
    (after compounding with prior discounts).
    ``applied_to_subtotal_after_prior`` is the running subtotal *at the
    moment this discount was applied*, useful for audit trails.
    """

    model_config = ConfigDict(frozen=True)

    kind: DiscountKind
    original_value: Decimal
    effective_twd: Decimal
    applied_to_subtotal_after_prior: Decimal


class DiscountResolveOutput(BaseModel):
    """Result of ``resolve_discounts``. ``net_revenue`` MAY be negative."""

    model_config = ConfigDict(frozen=True)

    net_revenue: Decimal
    discount_total: Decimal
    breakdown: list[ResolvedDiscount]


def _quantize_twd(value: Decimal) -> Decimal:
    """Quantize a TWD amount to 2dp using ROUND_HALF_UP."""
    return value.quantize(_TWD_QUANTUM, rounding=ROUND_HALF_UP)


def resolve_discounts(input: DiscountResolveInput) -> DiscountResolveOutput:
    """Resolve a stack of order discounts to a net revenue and breakdown.

    Pure function. No I/O, no globals.

    Stacking order (applied in this order regardless of input order):

        percent → employee → amount → allowance → comp

    Returned ``breakdown`` is in INPUT order so the caller can write each
    ``effective_twd`` back to its ``order_discounts`` row without resorting.

    Example::

        out = resolve_discounts(DiscountResolveInput(
            subtotal=Decimal("1000"),
            discounts=[
                DiscountRow(kind="percent", value=Decimal("0.10")),
                DiscountRow(kind="amount",  value=Decimal("50")),
            ],
        ))
        # out.net_revenue == Decimal("850.00")
        # out.discount_total == Decimal("150.00")

    Raises:
        pydantic.ValidationError: malformed input (float, out-of-range, ...).
    """
    n = len(input.discounts)

    # Pre-allocate per-input-index slots for effective_twd and
    # applied_to_subtotal_after_prior. We'll fill them as we walk the
    # stacking order, then emit ``breakdown`` in original input order.
    effective: list[Decimal] = [_ZERO] * n
    snapshot: list[Decimal] = [_ZERO] * n

    running = input.subtotal

    # Group indices by kind in input order so we preserve relative
    # ordering within each kind-group (AC-6 compounding, AC-12 stacking).
    by_kind: dict[DiscountKind, list[int]] = {k: [] for k in STACKING_ORDER}
    for idx, d in enumerate(input.discounts):
        by_kind[d.kind].append(idx)

    for kind in STACKING_ORDER:
        for idx in by_kind[kind]:
            d = input.discounts[idx]
            snapshot[idx] = running
            if kind in ("percent", "employee"):
                removed = running * d.value
                running = running - removed
                effective[idx] = removed
            elif kind in ("amount", "allowance"):
                running = running - d.value
                effective[idx] = d.value
            else:  # comp
                # Comp eats the rest of the order, regardless of value.
                # If running already non-positive, comp takes 0.
                if running > _ZERO:
                    effective[idx] = running
                    running = _ZERO
                else:
                    effective[idx] = _ZERO
                    # running stays as-is (may be 0 or negative)

    # Build breakdown in INPUT order, quantizing at the boundary.
    breakdown: list[ResolvedDiscount] = []
    discount_total_acc = _ZERO
    for idx, d in enumerate(input.discounts):
        eff_q = _quantize_twd(effective[idx])
        breakdown.append(
            ResolvedDiscount(
                kind=d.kind,
                original_value=d.value,
                effective_twd=eff_q,
                applied_to_subtotal_after_prior=_quantize_twd(snapshot[idx]),
            ),
        )
        discount_total_acc += eff_q

    net_revenue = _quantize_twd(input.subtotal - discount_total_acc)
    return DiscountResolveOutput(
        net_revenue=net_revenue,
        discount_total=_quantize_twd(discount_total_acc),
        breakdown=breakdown,
    )


__all__ = [
    "STACKING_ORDER",
    "DiscountKind",
    "DiscountResolveInput",
    "DiscountResolveOutput",
    "DiscountRow",
    "ResolvedDiscount",
    "resolve_discounts",
]
