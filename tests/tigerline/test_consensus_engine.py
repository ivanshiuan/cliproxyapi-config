"""V3.0 Sprint 2 — consensus engine tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tigerline.consensus_engine import compute_consensus
from tigerline.models import OddsSnapshot, SourceMetadata


def _snap(
    snapshot_id: str,
    bookmaker: str,
    *,
    line: str | None = "-1.75",
    price: str = "1.92",
    market_type: str = "AH_full",
) -> OddsSnapshot:
    meta = SourceMetadata.model_validate(
        {
            "source": "manual",
            "collected_at": datetime(2026, 6, 22, 21, 0, tzinfo=UTC),
            "source_confidence": "high",
            "is_verified": True,
            "notes": "",
        }
    )
    return OddsSnapshot.model_validate(
        {
            "snapshot_id": snapshot_id,
            "match_id": "m1",
            "bookmaker": bookmaker,
            "market_type": market_type,
            "selection": "home",
            "line": line,
            "price": price,
            "odds_format": "decimal",
            "metadata": meta.model_dump(mode="json"),
        }
    )


def test_empty_input_returns_none():
    assert compute_consensus([]) is None


def test_high_agreement_when_books_cluster():
    snaps = [_snap(f"s{i}", f"b{i}", line=lv) for i, lv in enumerate(["-1.75", "-1.75", "-1.75"])]
    r = compute_consensus(snaps)
    assert r is not None
    assert r.agreement == "high"
    assert r.consensus_line == Decimal("-1.75")


def test_medium_agreement_with_minor_spread():
    snaps = [_snap(f"s{i}", f"b{i}", line=lv) for i, lv in enumerate(["-1.75", "-1.5", "-2"])]
    r = compute_consensus(snaps)
    assert r is not None
    # spread = 0.25 → exceeds high_agreement_band (0.05) but within medium (0.15)? No, 0.25 > 0.15 → low
    assert r.agreement == "low"


def test_user_platform_better_than_market():
    """User has -1.5 vs consensus -1.75 → softer favorite → better for backer."""
    snaps = [
        _snap("p", "pinnacle", line="-1.75"),
        _snap("b", "bet365", line="-1.75"),
        _snap("u", "user_book", line="-1.5"),
    ]
    r = compute_consensus(snaps, user_bookmaker="user_book")
    assert r is not None
    assert r.user_platform_edge == "better_than_market"
    assert r.edge_magnitude == Decimal("0.25")


def test_user_platform_worse_than_market():
    snaps = [
        _snap("p", "pinnacle", line="-1.75"),
        _snap("b", "bet365", line="-1.75"),
        _snap("u", "user_book", line="-2.0"),
    ]
    r = compute_consensus(snaps, user_bookmaker="user_book")
    assert r is not None
    assert r.user_platform_edge == "worse_than_market"


def test_user_platform_in_line():
    snaps = [
        _snap("p", "pinnacle", line="-1.75"),
        _snap("u", "user_book", line="-1.75"),
    ]
    r = compute_consensus(snaps, user_bookmaker="user_book")
    assert r is not None
    assert r.user_platform_edge == "in_line"


def test_no_user_book_when_missing():
    snaps = [_snap("a", "pinnacle"), _snap("b", "bet365")]
    r = compute_consensus(snaps, user_bookmaker="missing")
    assert r is not None
    assert r.user_platform_edge == "no_user_book"


def test_outliers_flagged():
    snaps = [
        _snap("a", "pinnacle", line="-1.75"),
        _snap("b", "bet365", line="-1.75"),
        _snap("o", "weirdbook", line="-1.0"),
    ]
    r = compute_consensus(snaps)
    assert r is not None
    assert "weirdbook" in r.outlier_books


def test_no_user_book_when_user_has_null_line():
    snaps = [
        _snap("a", "pinnacle", line="-1.75"),
        _snap("b", "user_book", line=None, market_type="moneyline"),
    ]
    r = compute_consensus(snaps, user_bookmaker="user_book")
    assert r is not None
    assert r.user_platform_edge == "no_user_book"


def test_consensus_handles_all_null_lines():
    snaps = [
        _snap("a", "pinnacle", line=None, market_type="moneyline"),
        _snap("b", "bet365", line=None, market_type="moneyline"),
    ]
    r = compute_consensus(snaps)
    assert r is not None
    assert r.consensus_line is None
    assert r.agreement == "low"
