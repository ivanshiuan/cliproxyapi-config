"""Labor hours classifier — bucket a clock-in/out interval into 勞基法 buckets.

Implements the Taiwan 勞動基準法 (LSA) four-bucket model used by the
restaurant payroll subsystem:

    +-----------------------+--------+----------------+
    | bucket                | rate   | per-day max    |
    +-----------------------+--------+----------------+
    | regular_hours         | 1.00x  | 8h             |
    | overtime_tier1_hours  | 1.34x  | +2h (9-10h)    |
    | overtime_tier2_hours  | 1.67x  | +2h (11-12h)   |
    | holiday_hours         | 2.00x  | unbounded[1]   |
    +-----------------------+--------+----------------+

    [1] holiday-bucket hours come from a single segment whose calendar date
        sits in ``public_holidays``; the 12-hour daily ceiling does not
        apply because LSA Article 39 treats holiday overtime as a flat-rate
        band (scheduling-side responsibility, not classification-side).

Cross-midnight shifts are split at local-tz midnight; each calendar-day
segment is classified independently. ``classify_hours`` is pure: no I/O,
no globals, no logging.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator, model_validator

REGULAR_HOURS_CAP: Decimal = Decimal("8")
OVERTIME_TIER1_CAP: Decimal = Decimal("2")
OVERTIME_TIER2_CAP: Decimal = Decimal("2")
DAILY_HOURS_HARD_CEILING: Decimal = Decimal("12")
DEFAULT_TZ: str = "Asia/Taipei"

_HOUR_QUANT: Decimal = Decimal("0.01")
_SECONDS_PER_HOUR: Decimal = Decimal("3600")
_ZERO: Decimal = Decimal("0")
_ZERO_HOURS: Decimal = Decimal("0.00")
_MAX_SHIFT: timedelta = timedelta(hours=24)


class LaborLawViolationError(ValueError):
    """Raised when a single calendar day's non-holiday hours exceed 12."""


def _reject_float(v: object) -> object:
    if isinstance(v, float):
        raise ValueError("float is not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class LaborInput(BaseModel):
    """A single employee clock-in / clock-out interval to classify."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    employee_id: str
    clock_in: datetime
    clock_out: datetime
    public_holidays: list[date]
    tz: str = DEFAULT_TZ

    @field_validator("clock_in", "clock_out")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be tz-aware")
        return v

    @field_validator("tz")
    @classmethod
    def _tz_must_resolve(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"invalid IANA tz: {v}") from e
        return v

    @model_validator(mode="after")
    def _check_interval(self) -> LaborInput:
        if self.clock_out <= self.clock_in:
            raise ValueError("clock_out must be strictly after clock_in")
        if (self.clock_out - self.clock_in) > _MAX_SHIFT:
            raise ValueError("shift exceeds 24h; cross-midnight at most once")
        return self


class LaborBuckets(BaseModel):
    """Four-bucket breakdown of worked hours, all 2dp Decimals."""

    model_config = ConfigDict(frozen=True)

    employee_id: str
    regular_hours: Decimal
    overtime_tier1_hours: Decimal
    overtime_tier2_hours: Decimal
    holiday_hours: Decimal


def _seconds_to_hours(seconds: Decimal) -> Decimal:
    """Convert a Decimal seconds value to hours (no quantize — caller decides)."""
    return seconds / _SECONDS_PER_HOUR


def _segment_hours(start: datetime, end: datetime) -> Decimal:
    """Hours between two tz-aware datetimes, as a non-quantized Decimal."""
    delta = end - start
    seconds = Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1_000_000)
    )
    return _seconds_to_hours(seconds)


def _bucket_weekday_segment(
    hours: Decimal, segment_date: date
) -> tuple[Decimal, Decimal, Decimal]:
    """Split a single non-holiday segment's hours into (regular, ot1, ot2).

    Raises :class:`LaborLawViolationError` if the segment exceeds the 12h
    daily ceiling (法定上限).
    """
    if hours > DAILY_HOURS_HARD_CEILING:
        raise LaborLawViolationError(
            f"calendar day {segment_date.isoformat()} non-holiday hours "
            f"= {hours} exceed the 12h hard ceiling"
        )

    regular = min(hours, REGULAR_HOURS_CAP)
    remainder = hours - regular
    ot1 = min(remainder, OVERTIME_TIER1_CAP)
    remainder -= ot1
    ot2 = min(remainder, OVERTIME_TIER2_CAP)
    return regular, ot1, ot2


def classify_hours(input: LaborInput) -> LaborBuckets:
    """Classify a clock-in/out interval into 勞基法 hour buckets.

    Pure function. No I/O, no global state, no logging side-effects.

    Bucket multipliers (applied by the payroll module, NOT here):
        regular_hours        * 1.00
        overtime_tier1_hours * 1.34   (9th-10th hour)
        overtime_tier2_hours * 1.67   (11th-12th hour)
        holiday_hours        * 2.00   (whole segment falls in public_holidays)

    Example::

        buckets = classify_hours(LaborInput(
            employee_id="emp-1",
            clock_in=datetime(2024, 3, 15, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")),
            clock_out=datetime(2024, 3, 15, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
            public_holidays=[],
        ))
        # buckets.regular_hours == Decimal("8.00")
        # buckets.overtime_tier1_hours == Decimal("1.00")

    Raises:
        pydantic.ValidationError: malformed input (naive datetime,
            reversed range, bad tz, >24h shift).
        LaborLawViolationError: any single calendar day's
            ``regular + ot1 + ot2`` exceeds 12h.
    """
    tz = ZoneInfo(input.tz)
    local_in = input.clock_in.astimezone(tz)
    local_out = input.clock_out.astimezone(tz)

    date_in = local_in.date()
    date_out = local_out.date()
    holidays = set(input.public_holidays)

    segments: list[tuple[date, Decimal]]
    if date_in == date_out:
        segments = [(date_in, _segment_hours(local_in, local_out))]
    else:
        # Cross-midnight: split at midnight of date_out (the boundary point
        # belongs to segment B per the half-open rule).
        midnight = datetime.combine(date_out, time(0, 0), tzinfo=tz)
        segments = [
            (date_in, _segment_hours(local_in, midnight)),
            (date_out, _segment_hours(midnight, local_out)),
        ]

    total_regular = _ZERO
    total_ot1 = _ZERO
    total_ot2 = _ZERO
    total_holiday = _ZERO

    for seg_date, seg_hours in segments:
        if seg_hours <= _ZERO:
            continue
        if seg_date in holidays:
            total_holiday += seg_hours
        else:
            reg, ot1, ot2 = _bucket_weekday_segment(seg_hours, seg_date)
            total_regular += reg
            total_ot1 += ot1
            total_ot2 += ot2

    return LaborBuckets(
        employee_id=input.employee_id,
        regular_hours=total_regular.quantize(_HOUR_QUANT, rounding=ROUND_HALF_UP),
        overtime_tier1_hours=total_ot1.quantize(_HOUR_QUANT, rounding=ROUND_HALF_UP),
        overtime_tier2_hours=total_ot2.quantize(_HOUR_QUANT, rounding=ROUND_HALF_UP),
        holiday_hours=total_holiday.quantize(_HOUR_QUANT, rounding=ROUND_HALF_UP),
    )


__all__ = [
    "DAILY_HOURS_HARD_CEILING",
    "DEFAULT_TZ",
    "OVERTIME_TIER1_CAP",
    "OVERTIME_TIER2_CAP",
    "REGULAR_HOURS_CAP",
    "LaborBuckets",
    "LaborInput",
    "LaborLawViolationError",
    "StrictDecimal",
    "classify_hours",
]
