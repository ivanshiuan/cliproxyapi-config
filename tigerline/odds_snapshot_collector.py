"""V3.0 Sprint 1 — odds snapshot collector.

Dual-write: SQLite ``odds_snapshots`` (append-only) + ``data/odds_snapshots.jsonl``
mirror for grep / replay. Refuses snapshots that fail data-quality block list.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from tigerline.data_quality import DataIssue, check_snapshot, is_blocked
from tigerline.models import OddsSnapshot
from tigerline.storage import save_snapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSONL = REPO_ROOT / "data" / "odds_snapshots.jsonl"


class SnapshotRejected(Exception):
    """Raised when a snapshot fails the data-quality block list."""

    def __init__(self, issues: list[DataIssue]) -> None:
        super().__init__(
            "; ".join(f"[{i.code}] {i.message}" for i in issues if i.severity == "block")
        )
        self.issues = issues


def parse_snapshot(payload: str | bytes | dict) -> OddsSnapshot:
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    return OddsSnapshot.model_validate(data)


def load_snapshot(path: str | Path) -> OddsSnapshot:
    return parse_snapshot(Path(path).read_bytes())


def jsonl_path() -> Path:
    """Resolve the append-only JSONL mirror. Override via TIGER_SNAPSHOTS_JSONL."""
    env = os.environ.get("TIGER_SNAPSHOTS_JSONL")
    return Path(env) if env else DEFAULT_JSONL


def record_snapshot(
    conn: sqlite3.Connection,
    snap: OddsSnapshot,
    *,
    jsonl: Path | None = None,
) -> list[DataIssue]:
    """SQLite + JSONL dual-write after data-quality screening.

    Returns the (warn-only) issue list. Raises SnapshotRejected on block.
    """
    issues = check_snapshot(snap)
    if is_blocked(issues):
        raise SnapshotRejected(issues)

    save_snapshot(conn, snap)

    target = jsonl or jsonl_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(snap.model_dump_json() + "\n")

    return issues


def replay_jsonl(conn: sqlite3.Connection, jsonl: Path | None = None) -> int:
    """Replay every record in the JSONL back into SQLite (idempotent on PK)."""
    source = jsonl or jsonl_path()
    if not source.exists():
        return 0
    inserted = 0
    for line in _iter_jsonl(source):
        if not line.strip():
            continue
        snap = parse_snapshot(line)
        try:
            save_snapshot(conn, snap)
        except sqlite3.IntegrityError:
            continue
        inserted += 1
    return inserted


def _iter_jsonl(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8") as fh:
        yield from fh


__all__ = [
    "DEFAULT_JSONL",
    "SnapshotRejected",
    "jsonl_path",
    "load_snapshot",
    "parse_snapshot",
    "record_snapshot",
    "replay_jsonl",
]
