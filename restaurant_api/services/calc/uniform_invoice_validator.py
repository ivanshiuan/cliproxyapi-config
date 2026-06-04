"""Taiwan uniform invoice number (統一編號) validator.

Implements the 2023 Ministry of Finance algorithm:

    1. Normalize input (NFKC, strip whitespace + ASCII dashes).
    2. Reject anything that is not exactly 8 ASCII digits.
    3. Multiply each digit by weights ``[1, 2, 1, 2, 1, 2, 4, 1]``;
       if a product is two digits, sum the digits of the product.
    4. Sum the 8 adjusted products = S.
    5. If S mod 5 == 0, the number is valid.
    6. Special rule — if d7 == 7, then ``S + 1`` is also accepted (i.e.
       valid iff S mod 5 == 0 OR (S + 1) mod 5 == 0).

Pure function. No I/O, no global state, no caching, < 0.1ms per call.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict

_WEIGHTS: tuple[int, int, int, int, int, int, int, int] = (1, 2, 1, 2, 1, 2, 4, 1)
_TAX_ID_LENGTH: int = 8
_SEVEN_RULE_DIGIT: int = 7
_SEVEN_RULE_INDEX: int = 6  # 0-based index of d7
_CHECKSUM_MOD: int = 5

Reason = Literal["ok", "wrong_length", "non_numeric", "checksum_failed"]


class TaxIdValidationInput(BaseModel):
    """Raw user-entered tax ID."""

    model_config = ConfigDict(frozen=True, strict=True)

    raw: str


class TaxIdValidationResult(BaseModel):
    """Structured validation outcome."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    normalized: str | None
    reason: Reason


def _normalize(raw: str) -> str:
    """NFKC-fold the string, then strip ASCII whitespace and dashes.

    NFKC turns full-width digits (e.g. U+FF12 FULLWIDTH DIGIT TWO) into
    ASCII digits and collapses any other compatibility-mapped digit
    characters. We then drop whitespace and ASCII ``-`` to allow common
    human-typed shapes such as ``"220-99131"`` or ``" 22099131 "``.
    """
    folded = unicodedata.normalize("NFKC", raw)
    cleaned_chars: list[str] = []
    for ch in folded:
        if ch.isspace() or ch == "-":
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars)


def _checksum_valid(digits: tuple[int, ...]) -> bool:
    s = 0
    for d, w in zip(digits, _WEIGHTS, strict=True):
        product = d * w
        if product >= 10:
            product = (product // 10) + (product % 10)
        s += product

    if s % _CHECKSUM_MOD == 0:
        return True
    return (
        digits[_SEVEN_RULE_INDEX] == _SEVEN_RULE_DIGIT
        and (s + 1) % _CHECKSUM_MOD == 0
    )


def validate_uniform_invoice(input: TaxIdValidationInput) -> TaxIdValidationResult:
    """Validate a Taiwan uniform invoice number (統一編號).

    Pure function. No I/O.
    """
    normalized_candidate = _normalize(input.raw)

    if len(normalized_candidate) != _TAX_ID_LENGTH:
        return TaxIdValidationResult(
            is_valid=False, normalized=None, reason="wrong_length"
        )

    if not normalized_candidate.isascii() or not normalized_candidate.isdigit():
        return TaxIdValidationResult(
            is_valid=False, normalized=None, reason="non_numeric"
        )

    digits = tuple(int(c) for c in normalized_candidate)
    if _checksum_valid(digits):
        return TaxIdValidationResult(
            is_valid=True, normalized=normalized_candidate, reason="ok"
        )
    return TaxIdValidationResult(
        is_valid=False, normalized=normalized_candidate, reason="checksum_failed"
    )


__all__ = [
    "Reason",
    "TaxIdValidationInput",
    "TaxIdValidationResult",
    "validate_uniform_invoice",
]
