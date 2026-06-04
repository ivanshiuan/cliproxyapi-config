"""ACs for ``labor_hours_classifier.classify_hours``."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.labor_hours_classifier import (
    LaborBuckets,
    LaborInput,
    LaborLawViolationError,
    classify_hours,
)

TPE = ZoneInfo("Asia/Taipei")
EMP = "00000000-0000-0000-0000-000000000aaa"


def _input(
    clock_in: datetime,
    clock_out: datetime,
    holidays: list[date] | None = None,
    tz: str = "Asia/Taipei",
) -> LaborInput:
    return LaborInput(
        employee_id=EMP,
        clock_in=clock_in,
        clock_out=clock_out,
        public_holidays=holidays if holidays is not None else [],
        tz=tz,
    )


def test_ac01_eight_hours_all_regular() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 17, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("0.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")
    assert buckets.holiday_hours == Decimal("0.00")


def test_ac02_nine_hours_eight_reg_one_ot1() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 18, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("1.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")
    assert buckets.holiday_hours == Decimal("0.00")


def test_ac03_exactly_ten_hours() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 19, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("2.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")


def test_ac04_eleven_hours_eight_two_one() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 20, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("2.00")
    assert buckets.overtime_tier2_hours == Decimal("1.00")


def test_ac05_exactly_twelve_hours_no_raise() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 21, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("2.00")
    assert buckets.overtime_tier2_hours == Decimal("2.00")
    assert buckets.holiday_hours == Decimal("0.00")


def test_ac06_over_twelve_hours_raises() -> None:
    payload = _input(
        datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
        datetime(2024, 3, 15, 22, 0, tzinfo=TPE),
    )
    with pytest.raises(LaborLawViolationError):
        classify_hours(payload)


def test_ac07_holiday_all_day_twelve_hours() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 2, 28, 10, 0, tzinfo=TPE),
            datetime(2024, 2, 28, 22, 0, tzinfo=TPE),
            holidays=[date(2024, 2, 28)],
        )
    )
    assert buckets.holiday_hours == Decimal("12.00")
    assert buckets.regular_hours == Decimal("0.00")
    assert buckets.overtime_tier1_hours == Decimal("0.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")


def test_ac08_holiday_over_twelve_hours_still_ok() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 2, 28, 9, 0, tzinfo=TPE),
            datetime(2024, 2, 28, 22, 0, tzinfo=TPE),
            holidays=[date(2024, 2, 28)],
        )
    )
    assert buckets.holiday_hours == Decimal("13.00")
    assert buckets.regular_hours == Decimal("0.00")


def test_ac09_midnight_crossing_both_weekdays() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 22, 0, tzinfo=TPE),
            datetime(2024, 3, 16, 2, 0, tzinfo=TPE),
        )
    )
    # segment A = 2h regular (15th), segment B = 2h regular (16th)
    assert buckets.regular_hours == Decimal("4.00")
    assert buckets.overtime_tier1_hours == Decimal("0.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")
    assert buckets.holiday_hours == Decimal("0.00")


def test_ac10_midnight_crossing_into_holiday() -> None:
    buckets = classify_hours(
        _input(
            datetime(2024, 2, 27, 22, 0, tzinfo=TPE),
            datetime(2024, 2, 28, 4, 0, tzinfo=TPE),
            holidays=[date(2024, 2, 28)],
        )
    )
    # segment A (Feb 27, weekday) = 2h regular
    # segment B (Feb 28, holiday) = 4h holiday
    assert buckets.regular_hours == Decimal("2.00")
    assert buckets.holiday_hours == Decimal("4.00")
    assert buckets.overtime_tier1_hours == Decimal("0.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")


def test_ac11_midnight_crossing_long_shift_no_raise() -> None:
    # 14h total: 14:00 -> next-day 04:00, split as 10h + 4h
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 14, 0, tzinfo=TPE),
            datetime(2024, 3, 16, 4, 0, tzinfo=TPE),
        )
    )
    # segment A (15th) = 10h: reg=8, ot1=2
    # segment B (16th) = 4h: reg=4
    assert buckets.regular_hours == Decimal("12.00")
    assert buckets.overtime_tier1_hours == Decimal("2.00")
    assert buckets.overtime_tier2_hours == Decimal("0.00")
    assert buckets.holiday_hours == Decimal("0.00")


def test_ac12_single_day_thirteen_hours_raises() -> None:
    # 13h same-day → raise; but cross-midnight 13h split into 10+3 should not
    with pytest.raises(LaborLawViolationError):
        classify_hours(
            _input(
                datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
                datetime(2024, 3, 15, 22, 0, tzinfo=TPE),
            )
        )
    # cross-midnight 13h: 10h + 3h, both <=12h, no raise
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 14, 0, tzinfo=TPE),
            datetime(2024, 3, 16, 3, 0, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("11.00")  # 8 + 3
    assert buckets.overtime_tier1_hours == Decimal("2.00")


def test_ac13_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        LaborInput(
            employee_id=EMP,
            clock_in=datetime(2024, 3, 15, 9, 0),  # naive
            clock_out=datetime(2024, 3, 15, 17, 0, tzinfo=TPE),
            public_holidays=[],
        )


def test_ac14_reversed_range_rejected() -> None:
    with pytest.raises(ValidationError):
        LaborInput(
            employee_id=EMP,
            clock_in=datetime(2024, 3, 15, 17, 0, tzinfo=TPE),
            clock_out=datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            public_holidays=[],
        )


def test_ac15_zero_duration_rejected() -> None:
    same = datetime(2024, 3, 15, 9, 0, tzinfo=TPE)
    with pytest.raises(ValidationError):
        LaborInput(
            employee_id=EMP,
            clock_in=same,
            clock_out=same,
            public_holidays=[],
        )


def test_ac16_invalid_tz_rejected() -> None:
    with pytest.raises(ValidationError):
        LaborInput(
            employee_id=EMP,
            clock_in=datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            clock_out=datetime(2024, 3, 15, 17, 0, tzinfo=TPE),
            public_holidays=[],
            tz="Mars/Olympus_Mons",
        )


def test_ac17_fractional_hours_precision() -> None:
    # 09:00 -> 17:30 = 8.5h → reg=8.00, ot1=0.50
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 17, 30, tzinfo=TPE),
        )
    )
    assert buckets.regular_hours == Decimal("8.00")
    assert buckets.overtime_tier1_hours == Decimal("0.50")
    assert buckets.overtime_tier2_hours == Decimal("0.00")


def test_ac18_over_twentyfour_hours_rejected() -> None:
    with pytest.raises(ValidationError):
        LaborInput(
            employee_id=EMP,
            clock_in=datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            clock_out=datetime(2024, 3, 16, 10, 0, tzinfo=TPE),
            public_holidays=[],
        )


def test_ac19_total_hours_invariant() -> None:
    # 9h shift → sum of four buckets == 9.00
    buckets = classify_hours(
        _input(
            datetime(2024, 3, 15, 9, 0, tzinfo=TPE),
            datetime(2024, 3, 15, 18, 30, tzinfo=TPE),
        )
    )
    total = (
        buckets.regular_hours
        + buckets.overtime_tier1_hours
        + buckets.overtime_tier2_hours
        + buckets.holiday_hours
    )
    assert total == Decimal("9.50")
    assert isinstance(buckets, LaborBuckets)
