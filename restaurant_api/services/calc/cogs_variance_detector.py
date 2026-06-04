"""COGS variance detector — pure classification of one day's cost variance.

Given a single (store, business_date) row of theoretical COGS, actual COGS
and net revenue, classify the variance as ``ok`` / ``warning`` / ``critical``
according to a configurable threshold (default 5%).

Severity bands (based on ``|variance_pct|``):

    +-----------------+----------+--------+----------------------------+
    | Band            | severity | flag   | reason                     |
    +-----------------+----------+--------+----------------------------+
    | net_revenue<=0  | ok       | False  | no_revenue                 |
    | |pct| < T       | ok       | False  | ok                         |
    | T <= |pct| < 2T | warning  | True   | over_threshold             |
    | |pct| >= 2T, +  | critical | True   | critical_overrun           |
    | |pct| >= 2T, -  | critical | True   | critical_underrun          |
    +-----------------+----------+--------+----------------------------+

Pure function: no I/O, no globals, no logging. Caller is responsible for
sourcing the input (typically the ``mv_daily_pnl`` materialised view) and
acting on the report (writing alerts, sending Slack, etc.).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

DEFAULT_THRESHOLD_PCT: Decimal = Decimal("0.05")
CRITICAL_MULTIPLIER: Decimal = Decimal("2")

_MONEY_QUANT: Decimal = Decimal("0.01")
_PCT_QUANT: Decimal = Decimal("0.0001")
_ZERO: Decimal = Decimal("0")
_ZERO_MONEY: Decimal = Decimal("0.00")
_ZERO_PCT: Decimal = Decimal("0.0000")


def _reject_float(v: object) -> object:
    """Refuse ``float`` inputs to money / ratio fields.

    Pydantic v2's default lenient coercion will happily accept ``3000.0``
    where a ``Decimal`` is declared, which silently introduces binary-float
    rounding bugs. We block that at the gate.
    """
    if isinstance(v, float):
        raise ValueError("float is not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class VarianceInput(BaseModel):
    """Daily COGS-variance inputs sourced from ``mv_daily_pnl`` / cron job."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    store_id: str
    actual_cogs: StrictDecimal = Field(ge=_ZERO)
    theoretical_cogs: StrictDecimal = Field(ge=_ZERO)
    net_revenue: StrictDecimal
    threshold_pct: StrictDecimal = DEFAULT_THRESHOLD_PCT

    @field_validator("threshold_pct")
    @classmethod
    def _threshold_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= _ZERO:
            raise ValueError("threshold_pct must be > 0")
        return v


class VarianceReport(BaseModel):
    """Severity verdict for one day's COGS variance."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    store_id: str
    variance_abs: Decimal
    variance_pct: Decimal
    flag: bool
    severity: Literal["ok", "warning", "critical"]
    reason: str


def _quantize_money(v: Decimal) -> Decimal:
    return v.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _quantize_pct(v: Decimal) -> Decimal:
    return v.quantize(_PCT_QUANT, rounding=ROUND_HALF_UP)


def detect_variance(input: VarianceInput) -> VarianceReport:
    """Classify one day's COGS variance.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises ``pydantic.ValidationError`` on malformed input.

    Example::

        report = detect_variance(VarianceInput(
            business_date=date(2024, 3, 15),
            store_id="00000000-0000-0000-0000-000000000001",
            actual_cogs=Decimal("3500"),
            theoretical_cogs=Decimal("3000"),
            net_revenue=Decimal("10000"),
        ))
        # report.severity == "warning"; report.flag is True
    """
    variance_abs_raw = input.actual_cogs - input.theoretical_cogs
    variance_abs = _quantize_money(variance_abs_raw)

    if input.net_revenue <= _ZERO:
        return VarianceReport(
            business_date=input.business_date,
            store_id=input.store_id,
            variance_abs=variance_abs,
            variance_pct=_ZERO_PCT,
            flag=False,
            severity="ok",
            reason="no_revenue",
        )

    variance_pct_raw = variance_abs_raw / input.net_revenue
    variance_pct = _quantize_pct(variance_pct_raw)

    abs_pct = abs(variance_pct)
    t = input.threshold_pct
    critical_band = t * CRITICAL_MULTIPLIER

    severity: Literal["ok", "warning", "critical"]
    if abs_pct < t:
        severity = "ok"
        reason = "ok"
        flag = False
    elif abs_pct < critical_band:
        severity = "warning"
        reason = "over_threshold"
        flag = True
    else:
        severity = "critical"
        flag = True
        reason = "critical_overrun" if variance_abs_raw >= _ZERO else "critical_underrun"

    return VarianceReport(
        business_date=input.business_date,
        store_id=input.store_id,
        variance_abs=variance_abs,
        variance_pct=variance_pct,
        flag=flag,
        severity=severity,
        reason=reason,
    )


__all__ = [
    "CRITICAL_MULTIPLIER",
    "DEFAULT_THRESHOLD_PCT",
    "StrictDecimal",
    "VarianceInput",
    "VarianceReport",
    "detect_variance",
]
