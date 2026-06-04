"""ACs for ``cogs_variance_detector.detect_variance``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.cogs_variance_detector import (
    CRITICAL_MULTIPLIER,
    DEFAULT_THRESHOLD_PCT,
    VarianceInput,
    VarianceReport,
    detect_variance,
)

STORE = "00000000-0000-0000-0000-000000000001"
BIZ_DATE = date(2024, 3, 15)


def _make(
    *,
    actual: str,
    theoretical: str,
    net_revenue: str,
    threshold: str | None = None,
) -> VarianceInput:
    kwargs: dict[str, object] = {
        "business_date": BIZ_DATE,
        "store_id": STORE,
        "actual_cogs": Decimal(actual),
        "theoretical_cogs": Decimal(theoretical),
        "net_revenue": Decimal(net_revenue),
    }
    if threshold is not None:
        kwargs["threshold_pct"] = Decimal(threshold)
    return VarianceInput(**kwargs)


def test_ac01_happy_path_ok() -> None:
    report = detect_variance(
        _make(actual="3000", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("0.00")
    assert report.variance_pct == Decimal("0.0000")
    assert report.severity == "ok"
    assert report.flag is False
    assert report.reason == "ok"


def test_ac02_just_under_threshold() -> None:
    report = detect_variance(
        _make(actual="3499", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("499.00")
    assert report.variance_pct == Decimal("0.0499")
    assert report.severity == "ok"
    assert report.flag is False
    assert report.reason == "ok"


def test_ac03_exactly_at_threshold_is_warning() -> None:
    report = detect_variance(
        _make(actual="3500", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_pct == Decimal("0.0500")
    assert report.severity == "warning"
    assert report.flag is True
    assert report.reason == "over_threshold"


def test_ac04_just_over_threshold_warning() -> None:
    report = detect_variance(
        _make(actual="3501", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_pct == Decimal("0.0501")
    assert report.severity == "warning"
    assert report.flag is True
    assert report.reason == "over_threshold"


def test_ac05_double_threshold_is_critical_overrun() -> None:
    report = detect_variance(
        _make(actual="4000", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("1000.00")
    assert report.variance_pct == Decimal("0.1000")
    assert report.severity == "critical"
    assert report.flag is True
    assert report.reason == "critical_overrun"


def test_ac06_negative_variance_critical_underrun() -> None:
    report = detect_variance(
        _make(actual="1000", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("-2000.00")
    assert report.variance_pct == Decimal("-0.2000")
    assert report.severity == "critical"
    assert report.flag is True
    assert report.reason == "critical_underrun"


def test_ac07_negative_variance_warning_band() -> None:
    report = detect_variance(
        _make(actual="2400", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("-600.00")
    assert report.variance_pct == Decimal("-0.0600")
    assert report.severity == "warning"
    assert report.flag is True
    assert report.reason == "over_threshold"


def test_ac08_zero_net_revenue_is_no_revenue() -> None:
    report = detect_variance(
        _make(actual="3000", theoretical="2000", net_revenue="0")
    )
    assert report.variance_pct == Decimal("0.0000")
    assert report.severity == "ok"
    assert report.flag is False
    assert report.reason == "no_revenue"


def test_ac09_negative_net_revenue_is_no_revenue() -> None:
    report = detect_variance(
        _make(actual="3000", theoretical="2000", net_revenue="-100")
    )
    assert report.severity == "ok"
    assert report.flag is False
    assert report.reason == "no_revenue"
    assert report.variance_pct == Decimal("0.0000")


def test_ac10_custom_threshold_makes_2pct_critical() -> None:
    report = detect_variance(
        _make(
            actual="3000",
            theoretical="3200",
            net_revenue="10000",
            threshold="0.01",
        )
    )
    assert report.variance_pct == Decimal("-0.0200")
    assert report.severity == "critical"
    assert report.reason == "critical_underrun"


def test_ac11_zero_theoretical_cogs_no_divbyzero() -> None:
    report = detect_variance(
        _make(actual="500", theoretical="0", net_revenue="10000")
    )
    assert report.variance_abs == Decimal("500.00")
    assert report.variance_pct == Decimal("0.0500")
    assert report.severity == "warning"
    assert report.reason == "over_threshold"


def test_ac12_decimal_precision_4dp_variance_pct() -> None:
    report = detect_variance(
        _make(actual="3033", theoretical="3000", net_revenue="10000")
    )
    assert report.variance_pct == Decimal("0.0033")
    assert report.severity == "ok"


def test_ac13_float_input_rejected() -> None:
    with pytest.raises(ValidationError):
        VarianceInput(
            business_date=BIZ_DATE,
            store_id=STORE,
            actual_cogs=3000.0,  # type: ignore[arg-type]
            theoretical_cogs=Decimal("3000"),
            net_revenue=Decimal("10000"),
        )


def test_ac14_threshold_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        VarianceInput(
            business_date=BIZ_DATE,
            store_id=STORE,
            actual_cogs=Decimal("3000"),
            theoretical_cogs=Decimal("3000"),
            net_revenue=Decimal("10000"),
            threshold_pct=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        VarianceInput(
            business_date=BIZ_DATE,
            store_id=STORE,
            actual_cogs=Decimal("3000"),
            theoretical_cogs=Decimal("3000"),
            net_revenue=Decimal("10000"),
            threshold_pct=Decimal("-0.05"),
        )


def test_ac15_default_threshold_is_five_pct() -> None:
    assert Decimal("0.05") == DEFAULT_THRESHOLD_PCT
    assert Decimal("2") == CRITICAL_MULTIPLIER
    report = detect_variance(
        _make(actual="3499", theoretical="3000", net_revenue="10000")
    )
    # At default 5% threshold, 4.99% is still ok.
    assert report.severity == "ok"


def test_report_is_frozen() -> None:
    report = detect_variance(
        _make(actual="3000", theoretical="3000", net_revenue="10000")
    )
    assert isinstance(report, VarianceReport)
    with pytest.raises(ValidationError):
        report.flag = True  # type: ignore[misc]
