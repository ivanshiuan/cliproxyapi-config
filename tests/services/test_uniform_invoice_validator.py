"""ACs for ``uniform_invoice_validator.validate_uniform_invoice``."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from restaurant_api.services.calc.uniform_invoice_validator import (
    TaxIdValidationInput,
    TaxIdValidationResult,
    validate_uniform_invoice,
)


def _validate(raw: str) -> TaxIdValidationResult:
    return validate_uniform_invoice(TaxIdValidationInput(raw=raw))


def test_ac01_happy_path_12345675() -> None:
    # 12345675: d7=7, S=39, S+1=40, 40 % 5 == 0 → valid via 7-rule
    result = _validate("12345675")
    assert result.is_valid is True
    assert result.normalized == "12345675"
    assert result.reason == "ok"


def test_ac02_known_good_tsmc_22099131() -> None:
    # 22099131: S=30, mod 5 == 0 → valid
    result = _validate("22099131")
    assert result.is_valid is True
    assert result.normalized == "22099131"
    assert result.reason == "ok"


def test_ac03_known_good_96979933() -> None:
    # 96979933: S=50, mod 5 == 0 → valid (d7=3 so 7-rule does not fire)
    result = _validate("96979933")
    assert result.is_valid is True
    assert result.normalized == "96979933"
    assert result.reason == "ok"


def test_ac04_seven_rule_fires_for_10000073() -> None:
    # 10000073: d7=7, S=14, S+1=15 % 5 == 0 → valid only via the 7-rule.
    result = _validate("10000073")
    assert result.is_valid is True
    assert result.normalized == "10000073"
    assert result.reason == "ok"


def test_ac05_invalid_checksum_12345678() -> None:
    result = _validate("12345678")
    assert result.is_valid is False
    assert result.reason == "checksum_failed"


def test_ac06_wrong_length_too_short() -> None:
    result = _validate("1234567")
    assert result.is_valid is False
    assert result.normalized is None
    assert result.reason == "wrong_length"


def test_ac07_wrong_length_too_long() -> None:
    result = _validate("123456789")
    assert result.is_valid is False
    assert result.normalized is None
    assert result.reason == "wrong_length"


def test_ac08_non_numeric_with_letter() -> None:
    result = _validate("1234567A")
    assert result.is_valid is False
    assert result.normalized is None
    assert result.reason == "non_numeric"


def test_ac09_normalization_whitespace() -> None:
    result = _validate(" 22099131 ")
    assert result.is_valid is True
    assert result.normalized == "22099131"


def test_ac10_normalization_dashes() -> None:
    result = _validate("220-99131")
    assert result.is_valid is True
    assert result.normalized == "22099131"

    result2 = _validate("22-09-9131")
    assert result2.is_valid is True
    assert result2.normalized == "22099131"


def test_ac11_normalization_fullwidth_digits() -> None:
    # Full-width digits: U+FF12 etc., NFKC folds them to ASCII.
    result = _validate("２２０９９１３１")  # noqa: RUF001
    assert result.is_valid is True
    assert result.normalized == "22099131"


def test_ac12_empty_string_is_wrong_length() -> None:
    result = _validate("")
    assert result.is_valid is False
    assert result.normalized is None
    assert result.reason == "wrong_length"


def test_ac13_none_safety_strict_mode_rejects_none() -> None:
    with pytest.raises(ValidationError):
        TaxIdValidationInput(raw=None)  # type: ignore[arg-type]
    # Empty string itself must not raise from the validator.
    result = _validate("")
    assert isinstance(result, TaxIdValidationResult)


def test_ac14_performance_under_100us() -> None:
    iters = 1000
    start = time.perf_counter()
    for _ in range(iters):
        validate_uniform_invoice(TaxIdValidationInput(raw="22099131"))
    elapsed = time.perf_counter() - start
    avg_seconds = elapsed / iters
    # spec: < 0.1ms == 0.0001s per call on average
    assert avg_seconds < 0.0001, f"avg {avg_seconds * 1_000_000:.1f}us exceeds 100us"


def test_ac15_input_is_frozen() -> None:
    inp = TaxIdValidationInput(raw="22099131")
    with pytest.raises(ValidationError):
        inp.raw = "x"  # type: ignore[misc]


def test_mixed_halfwidth_and_fullwidth() -> None:
    # position 2 holds a full-width 0 (U+FF10)
    result = _validate("22０99131")  # noqa: RUF001
    assert result.is_valid is True
    assert result.normalized == "22099131"


def test_invalid_checksum_returns_normalized_form() -> None:
    # checksum_failed path still surfaces the normalized digits so the
    # caller can echo them back in the error UI.
    result = _validate("12345678")
    assert result.normalized == "12345678"


def test_letters_after_normalization_are_non_numeric() -> None:
    result = _validate("AB-CD-EFGH")
    assert result.is_valid is False
    assert result.reason == "non_numeric"
