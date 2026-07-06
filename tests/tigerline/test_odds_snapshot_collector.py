"""V3.0 Sprint 1 — snapshot collector round-trip + append-only enforcement."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tigerline.models import OddsSnapshot, SourceMetadata
from tigerline.odds_snapshot_collector import (
    SnapshotRejected,
    parse_snapshot,
    record_snapshot,
    replay_jsonl,
)
from tigerline.storage import connect, latest_snapshot, list_snapshots


def _meta_json(collected_at: str = "2026-06-22T21:00:00+00:00") -> dict:
    return SourceMetadata.model_validate(
        {
            "source": "manual",
            "collected_at": collected_at,
            "source_confidence": "high",
            "is_verified": True,
            "notes": "",
        }
    ).model_dump(mode="json")


def _snap(**overrides) -> OddsSnapshot:
    base = {
        "snapshot_id": "s_001",
        "match_id": "2026-wc-belgium-newzealand",
        "bookmaker": "pinnacle",
        "market_type": "AH_full",
        "selection": "Belgium",
        "line": "-1.75",
        "price": "1.92",
        "odds_format": "decimal",
        "metadata": _meta_json(),
    }
    base.update(overrides)
    return OddsSnapshot.model_validate(base)


def test_record_writes_both_sqlite_and_jsonl(db, tmp_path: Path):
    jsonl = tmp_path / "snaps.jsonl"
    snap = _snap()
    warnings = record_snapshot(db, snap, jsonl=jsonl)
    assert warnings == []

    rows = list_snapshots(db, snap.match_id)
    assert len(rows) == 1
    assert rows[0].snapshot_id == "s_001"

    assert jsonl.exists()
    line = jsonl.read_text(encoding="utf-8").strip()
    assert json.loads(line)["snapshot_id"] == "s_001"


def test_blocking_issue_rejects_write(db, tmp_path: Path):
    bad = _snap(line=None)  # AH_full requires a numeric line
    with pytest.raises(SnapshotRejected):
        record_snapshot(db, bad, jsonl=tmp_path / "x.jsonl")
    assert list_snapshots(db, bad.match_id) == []


def test_pk_conflict_on_duplicate(db, tmp_path: Path):
    record_snapshot(db, _snap(), jsonl=tmp_path / "j.jsonl")
    with pytest.raises(sqlite3.IntegrityError):
        record_snapshot(db, _snap(), jsonl=tmp_path / "j.jsonl")


def test_trigger_blocks_direct_update(db, tmp_path: Path):
    record_snapshot(db, _snap(), jsonl=tmp_path / "j.jsonl")
    with pytest.raises(sqlite3.IntegrityError) as ei:
        db.execute("UPDATE odds_snapshots SET price = '99' WHERE snapshot_id = 's_001'")
    assert "append-only" in str(ei.value)


def test_trigger_blocks_direct_delete(db, tmp_path: Path):
    record_snapshot(db, _snap(), jsonl=tmp_path / "j.jsonl")
    with pytest.raises(sqlite3.IntegrityError) as ei:
        db.execute("DELETE FROM odds_snapshots WHERE snapshot_id = 's_001'")
    assert "append-only" in str(ei.value)


def test_list_snapshots_filters_by_market_and_book(db, tmp_path: Path):
    j = tmp_path / "f.jsonl"
    record_snapshot(db, _snap(snapshot_id="s_a"), jsonl=j)
    record_snapshot(
        db,
        _snap(snapshot_id="s_b", market_type="totals_full", line="2.5", selection="Over"),
        jsonl=j,
    )
    record_snapshot(db, _snap(snapshot_id="s_c", bookmaker="bet365"), jsonl=j)

    all_rows = list_snapshots(db, "2026-wc-belgium-newzealand")
    assert len(all_rows) == 3

    ah_only = list_snapshots(db, "2026-wc-belgium-newzealand", market_type="AH_full")
    assert len(ah_only) == 2

    bet365_only = list_snapshots(db, "2026-wc-belgium-newzealand", bookmaker="bet365")
    assert len(bet365_only) == 1


def test_latest_snapshot_returns_most_recent(db, tmp_path: Path):
    j = tmp_path / "f.jsonl"
    record_snapshot(
        db,
        _snap(snapshot_id="early", metadata=_meta_json("2026-06-22T18:00:00+00:00")),
        jsonl=j,
    )
    record_snapshot(
        db,
        _snap(snapshot_id="late", metadata=_meta_json("2026-06-22T22:00:00+00:00")),
        jsonl=j,
    )
    latest = latest_snapshot(db, "2026-wc-belgium-newzealand", market_type="AH_full")
    assert latest is not None
    assert latest.snapshot_id == "late"


def test_parse_snapshot_accepts_dict_and_string():
    raw = {
        "snapshot_id": "p1",
        "match_id": "m1",
        "bookmaker": "b",
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "price": "1.95",
        "odds_format": "decimal",
        "metadata": _meta_json(),
    }
    assert parse_snapshot(raw) == parse_snapshot(json.dumps(raw))


def test_replay_into_fresh_db(db, tmp_path: Path):
    """Write two snapshots, then replay JSONL into a fresh database — should re-land them."""
    jsonl = tmp_path / "snaps.jsonl"
    record_snapshot(db, _snap(snapshot_id="r1"), jsonl=jsonl)
    record_snapshot(
        db,
        _snap(snapshot_id="r2", metadata=_meta_json("2026-06-22T22:00:00+00:00")),
        jsonl=jsonl,
    )

    fresh = connect(tmp_path / "fresh.db")
    try:
        inserted = replay_jsonl(fresh, jsonl=jsonl)
        assert inserted == 2
        assert len(list_snapshots(fresh, "2026-wc-belgium-newzealand")) == 2
    finally:
        fresh.close()


def test_replay_skips_duplicates(db, tmp_path: Path):
    jsonl = tmp_path / "snaps.jsonl"
    record_snapshot(db, _snap(snapshot_id="dup"), jsonl=jsonl)
    inserted = replay_jsonl(db, jsonl=jsonl)
    assert inserted == 0  # PK conflict swallowed silently
