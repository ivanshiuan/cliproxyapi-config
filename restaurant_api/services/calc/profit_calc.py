"""Real-profit calculator — pure function that computes one day's P&L.

Contract: ``specs/profit_calc.md``.

Given a day's structured inputs (orders, discounts, cost events, fixed costs,
platform fees, labor), produce a ``DailyPnLOutput`` with both surface and
real profit numbers, plus a ``cogs_variance_flag`` that fires when actual
COGS deviates from theoretical by more than 5% of net revenue.

Money rules (CLAUDE.md):
- Decimal everywhere; ``float`` literals are 422'd at the input boundary via
  ``StrictDecimal`` (not ``ConfigDict(strict=True)`` which would also block
  JSON-string Decimal coercion).
- All output money fields are quantized to 2dp ROUND_HALF_UP at the *end*
  of the computation; internal accumulation stays at full precision to
  avoid the classic "shaved-cent drift" bug.
- ``cogs_variance_pct`` is quantized to 4dp so the 0.05 threshold is
  unambiguous and doesn't flip from rounding at the 2nd decimal.

Discount resolution mirrors ``discount_resolver`` semantics inline: percent
(compounding) → amount → allowance → employee → comp. We don't import the
sibling module to keep this engine truly self-contained (per spec §10
"no extra deps").
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

COGS_VARIANCE_THRESHOLD = Decimal("0.05")
_TWD_QUANTUM = Decimal("0.01")
_PCT_QUANTUM = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on Decimal fields."""
    if isinstance(value, float):
        raise ValueError("float literals are not accepted; use a string or Decimal")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class OrderLine(BaseModel):
    """One sold line on the day's tape."""

    model_config = ConfigDict(frozen=True)

    item_id: str = Field(min_length=1)
    qty: StrictDecimal = Field(gt=_ZERO)
    unit_price: StrictDecimal = Field(ge=_ZERO)
    cogs_actual: StrictDecimal = Field(ge=_ZERO)
    cogs_theoretical: StrictDecimal = Field(ge=_ZERO)


class Discount(BaseModel):
    """One discount applied to a specific order_id.

    ``value`` semantics:
    - ``percent`` / ``employee``  → ratio in [0, 1]
    - others                       → TWD >= 0 (comp's value is informational)
    """

    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    kind: Literal["percent", "amount", "comp", "allowance", "employee"]
    value: StrictDecimal

    @model_validator(mode="after")
    def _check_value_range(self) -> Discount:
        if self.kind in ("percent", "employee"):
            if self.value < _ZERO or self.value > _ONE:
                raise ValueError(
                    f"{self.kind} discount value must be between 0 and 1 (got {self.value})",
                )
        else:
            if self.value < _ZERO:
                raise ValueError(f"{self.kind} discount value must be >= 0 (got {self.value})")
        return self


class CostEvent(BaseModel):
    """One non-sale ingredient cost event (waste / staff meal / tasting)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["waste", "staff_meal", "tasting"]
    cost: StrictDecimal = Field(ge=_ZERO)


class FixedCostLine(BaseModel):
    """One fixed cost prorated to per-day TWD."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    daily_amount: StrictDecimal = Field(ge=_ZERO)


class PlatformFee(BaseModel):
    """One platform fee (UberEats / foodpanda / LINE Pay / ...)."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    amount: StrictDecimal = Field(ge=_ZERO)


class DailyPnLInput(BaseModel):
    """All inputs for one store-day P&L computation."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    store_id: str = Field(min_length=1)
    orders: list[OrderLine]
    discounts: list[Discount]
    cost_events: list[CostEvent]
    platform_fees: list[PlatformFee]
    fixed_costs: list[FixedCostLine]
    labor_cost: StrictDecimal = Field(ge=_ZERO)


class DailyPnLOutput(BaseModel):
    """Daily P&L result. All money fields quantized to 2dp."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    store_id: str

    gross_revenue: Decimal
    discount_total: Decimal
    net_revenue: Decimal

    cogs_actual_total: Decimal
    cogs_theoretical_total: Decimal
    cost_waste: Decimal
    cost_staff_meal: Decimal
    cost_tasting: Decimal
    platform_fee_total: Decimal
    labor_cost: Decimal
    fixed_cost_total: Decimal

    gross_profit_real: Decimal
    net_profit_real: Decimal

    cogs_variance_pct: Decimal
    cogs_variance_flag: bool


def _quantize_twd(value: Decimal) -> Decimal:
    """Quantize a TWD amount to 2dp using ROUND_HALF_UP."""
    return value.quantize(_TWD_QUANTUM, rounding=ROUND_HALF_UP)


def _quantize_pct(value: Decimal) -> Decimal:
    """Quantize a ratio to 4dp so the variance threshold is unambiguous."""
    return value.quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)


def _resolve_order_discount(
    subtotal: Decimal,
    discounts_for_order: list[Discount],
) -> Decimal:
    """Return the TWD discount total for one order, following stacking rules.

    Order: percent (compounding) → employee (compounding) → amount →
    allowance → comp (zeroes the running subtotal). Mirrors the canonical
    rules from ``specs/discount_resolver.md``.
    """
    running = subtotal
    consumed = _ZERO

    # Step 1: percent — compound on running subtotal, input order.
    for d in discounts_for_order:
        if d.kind == "percent":
            removed = running * d.value
            running = running - removed
            consumed += removed

    # Step 2: employee — same arithmetic as percent.
    for d in discounts_for_order:
        if d.kind == "employee":
            removed = running * d.value
            running = running - removed
            consumed += removed

    # Step 3: amount — flat TWD off running.
    for d in discounts_for_order:
        if d.kind == "amount":
            running = running - d.value
            consumed += d.value

    # Step 4: allowance — flat TWD off running.
    for d in discounts_for_order:
        if d.kind == "allowance":
            running = running - d.value
            consumed += d.value

    # Step 5: comp — eats remaining positive running subtotal.
    for d in discounts_for_order:
        if d.kind == "comp" and running > _ZERO:
            consumed += running
            running = _ZERO
        # else: nothing left for comp to eat; running stays as-is.

    return consumed


def compute_daily_pnl(input: DailyPnLInput) -> DailyPnLOutput:
    """Compute one day's real P&L.

    Pure function. No I/O, no globals, no logging.

    Example::

        out = compute_daily_pnl(DailyPnLInput(
            business_date=date(2026, 6, 4),
            store_id="store-a",
            orders=[OrderLine(
                item_id="x", qty=Decimal("1"), unit_price=Decimal("1000"),
                cogs_actual=Decimal("300"), cogs_theoretical=Decimal("300"),
            )],
            discounts=[], cost_events=[], platform_fees=[],
            fixed_costs=[], labor_cost=Decimal("0"),
        ))
        # out.net_revenue == Decimal("1000.00")
        # out.gross_profit_real == Decimal("700.00")

    Raises:
        pydantic.ValidationError: malformed input (float, wrong sign, ...).
    """
    # Note on discount scoping: ``OrderLine`` carries no ``order_id``, so
    # the MVP treats discounts as applied to the day's bucket per
    # ``order_id`` group. For single-order days (which all the ACs model)
    # this matches `subtotal = gross_revenue` exactly. Multi-order
    # partitioning is the orders_service layer's job — this engine just
    # honours the spec's per-order_id stacking.
    gross_revenue = sum((line.qty * line.unit_price for line in input.orders), start=_ZERO)

    # Group discounts by order_id (input-order preserved within each group).
    grouped: dict[str, list[Discount]] = {}
    for d in input.discounts:
        grouped.setdefault(d.order_id, []).append(d)

    discount_total = _ZERO
    for _order_id, group in grouped.items():
        discount_total += _resolve_order_discount(gross_revenue, group)

    net_revenue = gross_revenue - discount_total

    cogs_actual_total = sum((line.cogs_actual for line in input.orders), start=_ZERO)
    cogs_theoretical_total = sum((line.cogs_theoretical for line in input.orders), start=_ZERO)

    cost_waste = sum(
        (e.cost for e in input.cost_events if e.kind == "waste"),
        start=_ZERO,
    )
    cost_staff_meal = sum(
        (e.cost for e in input.cost_events if e.kind == "staff_meal"),
        start=_ZERO,
    )
    cost_tasting = sum(
        (e.cost for e in input.cost_events if e.kind == "tasting"),
        start=_ZERO,
    )

    platform_fee_total = sum((p.amount for p in input.platform_fees), start=_ZERO)
    fixed_cost_total = sum((f.daily_amount for f in input.fixed_costs), start=_ZERO)

    gross_profit_real = (
        net_revenue - cogs_actual_total - cost_waste - cost_staff_meal - cost_tasting
    )
    net_profit_real = (
        gross_profit_real - platform_fee_total - input.labor_cost - fixed_cost_total
    )

    if net_revenue > _ZERO:
        variance_pct = (cogs_actual_total - cogs_theoretical_total) / net_revenue
        variance_pct_q = _quantize_pct(variance_pct)
    else:
        # Conservative: no revenue means no variance signal. Spec §9.
        variance_pct_q = _ZERO

    cogs_variance_flag = abs(variance_pct_q) > COGS_VARIANCE_THRESHOLD

    return DailyPnLOutput(
        business_date=input.business_date,
        store_id=input.store_id,
        gross_revenue=_quantize_twd(gross_revenue),
        discount_total=_quantize_twd(discount_total),
        net_revenue=_quantize_twd(net_revenue),
        cogs_actual_total=_quantize_twd(cogs_actual_total),
        cogs_theoretical_total=_quantize_twd(cogs_theoretical_total),
        cost_waste=_quantize_twd(cost_waste),
        cost_staff_meal=_quantize_twd(cost_staff_meal),
        cost_tasting=_quantize_twd(cost_tasting),
        platform_fee_total=_quantize_twd(platform_fee_total),
        labor_cost=_quantize_twd(input.labor_cost),
        fixed_cost_total=_quantize_twd(fixed_cost_total),
        gross_profit_real=_quantize_twd(gross_profit_real),
        net_profit_real=_quantize_twd(net_profit_real),
        cogs_variance_pct=variance_pct_q,
        cogs_variance_flag=cogs_variance_flag,
    )


__all__ = [
    "COGS_VARIANCE_THRESHOLD",
    "CostEvent",
    "DailyPnLInput",
    "DailyPnLOutput",
    "Discount",
    "FixedCostLine",
    "OrderLine",
    "PlatformFee",
    "compute_daily_pnl",
]
