"""V3.0 Sprint 2 — line movement engine tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tigerline.line_movement_analyzer import (
    analyze_movement,
    detect_reverse_line_movement,
    detect_steam_move,
)
from tigerline.models import OddsSnapshot, SourceMetadata


def _snap(
    snapshot_id: str,
    *,
    line: str = "-1.5",
    price: str = "1.92",
    bookmaker: str = "pinnacle",
    hours_offset: float = 0.0,
) -> OddsSnapshot:
    meta = SourceMetadata.model_validate(
        {
            "source": "manual",
            "collected_at": datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
            + timedelta(hours=hours_offset),
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
            "market_type": "AH_full",
            "selection": "home",
            "line": line,
            "price": price,
            "odds_format": "decimal",
            "metadata": meta.model_dump(mode="json"),
        }
    )


def test_analyze_returns_none_for_fewer_than_two_snapshots():
    assert analyze_movement([_snap("a")]) is None
    assert analyze_movement([]) is None


def test_analyze_detects_favorite_strengthening():
    snaps = [
        _snap("a", line="-1.5", hours_offset=0),
        _snap("b", line="-2.0", hours_offset=3),
    ]
    r = analyze_movement(snaps)
    assert r is not None
    assert r.line_delta == Decimal("-0.5")
    assert r.direction == "favorite_strengthening"
    assert r.opening_line == Decimal("-1.5")
    assert r.current_line == Decimal("-2.0")


def test_analyze_detects_favorite_weakening():
    snaps = [
        _snap("a", line="-2.0", hours_offset=0),
        _snap("b", line="-1.5", hours_offset=3),
    ]
    r = analyze_movement(snaps)
    assert r is not None
    assert r.direction == "favorite_weakening"


def test_analyze_stable_within_band():
    snaps = [
        _snap("a", line="-1.5", hours_offset=0),
        _snap("b", line="-1.5", hours_offset=2),
    ]
    r = analyze_movement(snaps)
    assert r is not None
    assert r.direction == "stable"
    assert r.velocity == "none"


def test_velocity_fast_over_short_window():
    snaps = [
        _snap("a", line="-1.0", hours_offset=0),
        _snap("b", line="-2.0", hours_offset=0.5),  # 1.0 in 0.5h = 2/h → fast
    ]
    r = analyze_movement(snaps)
    assert r is not None
    assert r.velocity == "fast"


def test_crosses_key_handicap():
    snaps = [
        _snap("a", line="-0.5", hours_offset=0),
        _snap("b", line="-2.0", hours_offset=3),
    ]
    r = analyze_movement(snaps)
    assert r is not None
    # Crosses -1, -1.5; -0.5 is exactly opening, -2 is exactly closing → those don't count.
    assert Decimal("-1") in r.crosses_key_handicaps
    assert Decimal("-1.5") in r.crosses_key_handicaps


def test_steam_move_three_books_same_direction():
    r1 = analyze_movement(
        [_snap("a1", line="-1.5", bookmaker="b1"), _snap("a2", line="-2", bookmaker="b1", hours_offset=2)]
    )
    r2 = analyze_movement(
        [_snap("b1", line="-1.5", bookmaker="b2"), _snap("b2", line="-2", bookmaker="b2", hours_offset=2)]
    )
    r3 = analyze_movement(
        [_snap("c1", line="-1.5", bookmaker="b3"), _snap("c2", line="-2", bookmaker="b3", hours_offset=2)]
    )
    assert r1 and r2 and r3
    assert detect_steam_move([r1, r2, r3]) is True


def test_no_steam_when_books_diverge():
    r1 = analyze_movement(
        [_snap("a1", line="-1.5", bookmaker="b1"), _snap("a2", line="-2", bookmaker="b1", hours_offset=2)]
    )
    r2 = analyze_movement(
        [_snap("b1", line="-1.5", bookmaker="b2"), _snap("b2", line="-1.0", bookmaker="b2", hours_offset=2)]
    )
    assert r1 and r2
    assert detect_steam_move([r1, r2]) is False  # opposite directions


def test_reverse_line_movement():
    r = analyze_movement(
        [_snap("a", line="-1.5", hours_offset=0), _snap("b", line="-1.0", hours_offset=2)]
    )
    assert r is not None
    # Public bet favorite (home), line weakens → RLM
    assert detect_reverse_line_movement(r, public_side="home", favorite_side="home") is True
    # Public bet underdog (away), line strengthens — opposite case
    r2 = analyze_movement(
        [_snap("a", line="-1.5", hours_offset=0), _snap("b", line="-2", hours_offset=2)]
    )
    assert r2 is not None
    assert detect_reverse_line_movement(r2, public_side="away", favorite_side="home") is True


def test_snapshots_sorted_internally_regardless_of_input():
    snaps = [
        _snap("late", line="-2", hours_offset=3),
        _snap("early", line="-1", hours_offset=0),
    ]
    r = analyze_movement(snaps)
    assert r is not None
    assert r.opening_line == Decimal("-1")
    assert r.current_line == Decimal("-2")
