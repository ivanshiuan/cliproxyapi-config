"""V3.0 Sprint 4 — CLV tracker tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tigerline.clv_tracker import (
    clv_summary,
    compute_clv,
    save_clv,
)


def _base(**overrides):
    base = {
        "clv_id": "c1",
        "match_id": "m1",
        "bookmaker": "pinnacle",
        "market_type": "AH_full",
        "selection": "home",
        "entry_line": Decimal("-1.5"),
        "entry_price": Decimal("1.95"),
        "entry_at": datetime(2026, 6, 22, 18, 0, tzinfo=UTC),
        "closing_line": Decimal("-2"),
        "closing_price": Decimal("1.85"),
        "closing_at": datetime(2026, 6, 22, 22, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def test_back_favorite_beat_when_line_strengthens():
    """Entered at -1.5, closed -2 → we got the softer favorite line → BEAT."""
    r = compute_clv(**_base())
    assert r.clv_result == "beat"
    assert r.clv_margin == Decimal("0.5")


def test_back_favorite_worse_when_line_weakens():
    r = compute_clv(**_base(entry_line=Decimal("-2"), closing_line=Decimal("-1.5")))
    assert r.clv_result == "worse"


def test_flat_within_dead_band():
    r = compute_clv(**_base(entry_line=Decimal("-1.75"), closing_line=Decimal("-1.75")))
    assert r.clv_result == "flat"
    assert r.clv_margin == Decimal("0")


def test_back_underdog_beat_when_line_shrinks():
    """Entered at +1.5 underdog, closed +1 → we got the bigger plus → BEAT."""
    r = compute_clv(
        **_base(entry_line=Decimal("1.5"), closing_line=Decimal("1.0")),
        selection_side="back_underdog",
    )
    assert r.clv_result == "beat"


def test_moneyline_uses_price_when_no_line():
    """No line → price comparison. closing_price > entry_price → worse value (we got lower)."""
    r = compute_clv(
        **_base(
            entry_line=None, closing_line=None,
            entry_price=Decimal("1.80"), closing_price=Decimal("1.90"),
            market_type="moneyline",
        )
    )
    assert r.clv_result == "worse"


def test_moneyline_beat_when_closing_price_drops():
    r = compute_clv(
        **_base(
            entry_line=None, closing_line=None,
            entry_price=Decimal("2.10"), closing_price=Decimal("1.95"),
            market_type="moneyline",
        )
    )
    assert r.clv_result == "beat"


def test_save_clv_round_trip(db):
    r = compute_clv(**_base())
    save_clv(db, r)
    row = db.execute("SELECT clv_result, clv_margin FROM clv_records WHERE clv_id = ?",
                     (r.clv_id,)).fetchone()
    assert row["clv_result"] == "beat"
    assert Decimal(row["clv_margin"]) == Decimal("0.5")


def test_save_clv_append_only(db):
    r = compute_clv(**_base())
    save_clv(db, r)
    with pytest.raises(sqlite3.IntegrityError):
        # Same clv_id PK conflict.
        save_clv(db, r)


def test_clv_records_no_update_trigger(db):
    r = compute_clv(**_base())
    save_clv(db, r)
    with pytest.raises(sqlite3.IntegrityError) as ei:
        db.execute("UPDATE clv_records SET clv_result = 'flat' WHERE clv_id = 'c1'")
    assert "append-only" in str(ei.value)


def test_clv_summary_aggregates_beat_rate(db):
    save_clv(db, compute_clv(**_base(clv_id="a")))
    save_clv(db, compute_clv(**_base(clv_id="b")))
    save_clv(db, compute_clv(
        **_base(clv_id="c", entry_line=Decimal("-2"), closing_line=Decimal("-1.5"))
    ))  # worse
    summary = clv_summary(db)
    assert summary["total"] == 3
    assert summary["beat"] == 2
    assert summary["worse"] == 1
    assert summary["beat_rate"] == pytest.approx(2 / 3)


def test_clv_summary_empty_when_no_records(db):
    summary = clv_summary(db)
    assert summary["total"] == 0
    assert summary["beat_rate"] == 0.0
