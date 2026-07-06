"""V3.0 Sprint 5 — Compliance guard tests."""

from __future__ import annotations

import pytest

from tigerline.compliance_guard import (
    DEFAULT_DISCLAIMER_EN,
    DEFAULT_DISCLAIMER_ZH,
    ComplianceError,
    append_disclaimer,
    check_text,
    enforce,
)


def test_clean_text_passes():
    assert check_text("This is a model output with no forbidden phrases.") == []


def test_blocks_guaranteed_profit_english():
    violations = check_text("This pick offers guaranteed profit on Sunday.")
    assert violations
    assert any("guaranteed profit" in v.match.lower() for v in violations)


def test_blocks_baozheng_huoli_chinese():
    violations = check_text("我們保證獲利")
    assert violations


def test_blocks_must_hit_chinese():
    assert check_text("本場必中") != []


def test_blocks_cant_lose():
    assert check_text("you literally can't lose") != []


def test_blocks_risk_free():
    assert check_text("a risk-free play this weekend") != []
    assert check_text("risk free entry") != []


def test_enforce_raises_on_violation():
    with pytest.raises(ComplianceError) as ei:
        enforce("This is guaranteed profit content.")
    assert ei.value.violations


def test_enforce_appends_disclaimer_when_clean():
    result = enforce("Pre-match brief: Belgium two-goal landing scenario.")
    assert DEFAULT_DISCLAIMER_ZH in result
    assert DEFAULT_DISCLAIMER_EN in result


def test_append_disclaimer_idempotent():
    once = append_disclaimer("hello")
    twice = append_disclaimer(once)
    assert once == twice
    assert twice.count(DEFAULT_DISCLAIMER_ZH) == 1


def test_check_text_returns_excerpt():
    violations = check_text("xx 必中 yy")
    assert violations
    assert "必中" in violations[0].excerpt
