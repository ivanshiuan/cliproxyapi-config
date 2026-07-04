"""V3.0 Sprint 1 — data-quality guard tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tigerline.data_quality import (
    check_metadata,
    check_snapshot,
    confidence_score,
    is_blocked,
)
from tigerline.models import OddsSnapshot, SourceMetadata


def _meta(**overrides) -> SourceMetadata:
    base = {
        "source": "manual",
        "collected_at": datetime(2026, 6, 22, 21, 0, tzinfo=UTC),
        "source_confidence": "high",
        "is_verified": True,
        "notes": "",
    }
    base.update(overrides)
    return SourceMetadata.model_validate(base)


def test_clean_metadata_yields_no_issues():
    assert check_metadata(_meta()) == []


def test_naive_timestamp_blocks():
    meta = _meta(collected_at=datetime(2026, 6, 22, 21, 0))  # no tzinfo
    issues = check_metadata(meta)
    assert is_blocked(issues)
    assert any(i.code == "naive_timestamp" for i in issues)


def test_future_timestamp_blocks():
    now = datetime(2026, 6, 22, tzinfo=UTC)
    meta = _meta(collected_at=now + timedelta(days=10))
    issues = check_metadata(meta, now=now)
    assert is_blocked(issues)
    assert any(i.code == "future_timestamp" for i in issues)


def test_low_confidence_manual_warns_but_passes():
    issues = check_metadata(_meta(source_confidence="low"))
    assert not is_blocked(issues)
    assert any(i.code == "low_confidence_manual" for i in issues)


def test_snapshot_requires_line_for_handicap_markets():
    raw = {
        "snapshot_id": "x",
        "match_id": "y",
        "bookmaker": "b",
        "market_type": "AH_full",
        "selection": "Team",
        "line": None,
        "price": "1.9",
        "odds_format": "decimal",
        "metadata": _meta().model_dump(mode="json"),
    }
    snap = OddsSnapshot.model_validate(raw)
    issues = check_snapshot(snap)
    assert is_blocked(issues)
    assert any(i.code == "missing_line" for i in issues)


def test_snapshot_spurious_line_for_moneyline_warns():
    raw = {
        "snapshot_id": "x",
        "match_id": "y",
        "bookmaker": "b",
        "market_type": "moneyline",
        "selection": "home",
        "line": "0.5",
        "price": "1.9",
        "odds_format": "decimal",
        "metadata": _meta().model_dump(mode="json"),
    }
    snap = OddsSnapshot.model_validate(raw)
    issues = check_snapshot(snap)
    assert not is_blocked(issues)
    assert any(i.code == "spurious_line" for i in issues)


def test_confidence_score_bands():
    assert confidence_score(_meta(source_confidence="high", is_verified=True)) == 95
    assert confidence_score(_meta(source_confidence="high", is_verified=False)) == 90
    assert confidence_score(_meta(source_confidence="medium", is_verified=False)) == 70
    assert confidence_score(_meta(source_confidence="low", is_verified=False)) == 40


def test_float_line_rejected_by_pydantic():
    """StrictDecimal guard still applies to OddsSnapshot."""
    raw = {
        "snapshot_id": "x",
        "match_id": "y",
        "bookmaker": "b",
        "market_type": "AH_full",
        "selection": "Team",
        "line": -1.75,  # bare float
        "price": "1.9",
        "odds_format": "decimal",
        "metadata": _meta().model_dump(mode="json"),
    }
    with pytest.raises(ValidationError):
        OddsSnapshot.model_validate(raw)
