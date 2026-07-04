"""Deny-list based redactor.

Runs a single pass over the text; any regex hit is replaced by
``[REDACTED:<kind>]`` and counted. If ANY rule hits, callers should mark
the containing document as ``is_sensitive=True``.

Order matters: earlier rules take priority when patterns overlap
(sk-ant is checked before generic sk-). But regexes are also written so
they don't overlap (word boundaries + explicit prefix segments).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict

RedactionKind = Literal[
    "bank_account",
    "credit_card",
    "passport",
    "national_id",
    "api_key",
]


# The single source of truth. Each entry is (compiled regex, kind).
DENY_LIST: list[tuple[re.Pattern[str], RedactionKind]] = [
    # ── Anthropic-specific must come before the generic sk- rule, so the
    # ── longer key is matched intact.
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), "api_key"),
    # Generic OpenAI-style sk-<20+ alnum>
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "api_key"),
    # GitHub personal / fine-grained access tokens
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "api_key"),
    # Google API keys
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"), "api_key"),
    # Taiwan national ID: 1 letter + 1|2 + 8 digits
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "national_id"),
    # ROC passport: 2 letters + 7 digits
    (re.compile(r"\b[A-Z]{2}\d{7}\b"), "passport"),
    # Credit card: 16 digits (must come before 14-digit bank account so
    # the longer match wins on the same span)
    (re.compile(r"\b\d{16}\b"), "credit_card"),
    # TW bank account: 14 digits
    (re.compile(r"\b\d{14}\b"), "bank_account"),
]


class RedactionHit(BaseModel):
    """One aggregated line per kind that fired at least once."""

    model_config = ConfigDict(frozen=True)
    kind: RedactionKind
    count: int


def redact(text: str) -> tuple[str, list[RedactionHit]]:
    """Return ``(redacted_text, hits)``.

    ``hits`` is empty iff nothing matched; non-empty implies the caller
    must mark the document as sensitive.
    """
    counter: Counter[RedactionKind] = Counter()
    out = text
    for pattern, kind in DENY_LIST:
        # findall for the count; sub for the replacement.
        matches = pattern.findall(out)
        if not matches:
            continue
        counter[kind] += len(matches)
        out = pattern.sub(f"[REDACTED:{kind}]", out)
    hits = [RedactionHit(kind=k, count=c) for k, c in counter.items()]
    return out, hits


__all__ = ["DENY_LIST", "RedactionHit", "RedactionKind", "redact"]
