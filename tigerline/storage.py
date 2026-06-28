"""SQLite persistence for TIGER LINE PRIME.

Uses sqlite3 stdlib + hand-rolled init.sql.
Why not SQLAlchemy/Alembic? Single-writer CLI tool, no relations beyond FK-by-string,
SQLAlchemy is overkill. The repo's SQLAlchemy stack is dedicated to the Postgres
restaurant_api; segregation is a feature.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from tigerline.models import (
    BetPlan,
    MatchClassification,
    MatchInput,
    OddsSnapshot,
    ReviewResult,
    SourceMetadata,
)

SCHEMA_VERSION = 3

# Migrations to run when ``PRAGMA user_version`` is below SCHEMA_VERSION.
# Index is the target version (1 → init.sql, 2+ → migrations/0NN_*.sql, ...).
# ``init.sql`` covers everything up to v1; v2+ live as separate files so
# rolling out new V3 sprints stays additive and reviewable.
_MIGRATIONS: dict[int, str] = {
    2: "migrations/002_v3_snapshots.sql",
    3: "migrations/003_v3_sprint4.sql",
}


def default_db_path() -> Path:
    """Resolve the SQLite path.

    Order: ``TIGER_DB_PATH`` env > ``~/.tigerline/tigerline.db``.
    Parent directories are created on first access.
    """
    env = os.environ.get("TIGER_DB_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".tigerline" / "tigerline.db"


def _init_sql_path() -> Path:
    return Path(__file__).parent / "sql" / "init.sql"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and lazily initialise) the TIGER LINE SQLite db."""
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

    # Fresh database — apply v1 init.sql first.
    if current < 1:
        conn.executescript(_init_sql_path().read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 1")
        current = 1

    # Then walk forward through every migration file.
    sql_dir = _init_sql_path().parent
    for version in range(current + 1, SCHEMA_VERSION + 1):
        rel = _MIGRATIONS.get(version)
        if rel is None:
            raise RuntimeError(f"missing migration for schema version {version}")
        conn.executescript((sql_dir / rel).read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {version}")

    conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# CRUD — every write uses ``OR REPLACE`` so re-running ``tiger analyze`` is
# idempotent (same match_id overwrites). The recommendations & classifications
# tables keep history via the composite (match_id, created_at) PK.
# ──────────────────────────────────────────────────────────────────────────


def save_match(conn: sqlite3.Connection, match: MatchInput) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO matches (match_id, kickoff_utc, home, away, input_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            match.match_id,
            match.kickoff_utc.isoformat(),
            match.home,
            match.away,
            match.model_dump_json(),
        ),
    )
    conn.commit()


def save_classification(
    conn: sqlite3.Connection, match_id: str, c: MatchClassification
) -> None:
    conn.execute(
        "INSERT INTO classifications (match_id, scenario, confidence, reasons_json) "
        "VALUES (?, ?, ?, ?)",
        (match_id, c.scenario, str(c.confidence), json.dumps(c.reasons)),
    )
    conn.commit()


def save_recommendation(
    conn: sqlite3.Connection, plan: BetPlan, bankroll: Decimal
) -> None:
    conn.execute(
        "INSERT INTO recommendations (match_id, plan_json, bankroll) VALUES (?, ?, ?)",
        (plan.match_id, plan.model_dump_json(), str(bankroll)),
    )
    conn.commit()


def save_review(conn: sqlite3.Connection, r: ReviewResult) -> None:
    hg, ag = r.actual_score
    conn.execute(
        "INSERT OR REPLACE INTO results (match_id, home_goals, away_goals, review_json) "
        "VALUES (?, ?, ?, ?)",
        (r.match_id, hg, ag, r.model_dump_json()),
    )
    for s in r.suggestions:
        conn.execute(
            "INSERT INTO rule_updates (match_id, suggestion, applied) VALUES (?, ?, 0)",
            (r.match_id, s),
        )
    conn.commit()


def load_match(conn: sqlite3.Connection, match_id: str) -> MatchInput | None:
    row = conn.execute(
        "SELECT input_json FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    if row is None:
        return None
    return MatchInput.model_validate_json(row["input_json"])


def latest_plan(conn: sqlite3.Connection, match_id: str) -> BetPlan | None:
    row = conn.execute(
        "SELECT plan_json FROM recommendations WHERE match_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return BetPlan.model_validate_json(row["plan_json"])


def list_backlog(conn: sqlite3.Connection) -> list[str]:
    """Match IDs with a recommendation but no result yet."""
    rows = conn.execute(
        "SELECT DISTINCT r.match_id FROM recommendations r "
        "LEFT JOIN results res ON res.match_id = r.match_id "
        "WHERE res.match_id IS NULL"
    ).fetchall()
    return [row["match_id"] for row in rows]


def stats_summary(
    conn: sqlite3.Connection,
    *,
    scenario: str | None = None,
    since: str | None = None,
) -> dict:
    """Aggregate review stats — by scenario, optionally filtered."""
    where = ["1=1"]
    params: list = []
    if scenario:
        where.append("c.scenario = ?")
        params.append(scenario)
    if since:
        where.append("res.reviewed_at >= ?")
        params.append(since)

    sql = (
        "SELECT c.scenario, res.review_json FROM results res "
        "JOIN classifications c ON c.match_id = res.match_id "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY res.match_id"
    )
    rows = conn.execute(sql, params).fetchall()
    by_scenario: dict[str, dict[str, int]] = {}
    for row in rows:
        rev = ReviewResult.model_validate_json(row["review_json"])
        bucket = by_scenario.setdefault(
            row["scenario"], {"n": 0, "scenario_correct": 0, "corridor_hit": 0, "main_win": 0}
        )
        bucket["n"] += 1
        bucket["scenario_correct"] += int(rev.scenario_correct)
        bucket["corridor_hit"] += int(rev.corridor_hit)
        bucket["main_win"] += int(rev.main_result in ("win", "half_win"))
    return by_scenario


def pending_rule_updates(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        "SELECT id, match_id, suggestion, created_at FROM rule_updates WHERE applied = 0 "
        "ORDER BY created_at DESC"
    ).fetchall()


# ──────────────────────────────────────────────────────────────────────────
# V3.0 Sprint 1 — odds_snapshots CRUD (append-only).
# ──────────────────────────────────────────────────────────────────────────


def save_snapshot(conn: sqlite3.Connection, snap: OddsSnapshot) -> None:
    """Insert one snapshot. Append-only — re-inserting the same ``snapshot_id``
    raises ``sqlite3.IntegrityError``.
    """
    conn.execute(
        "INSERT INTO odds_snapshots ("
        "  snapshot_id, match_id, bookmaker, market_type, selection, line, price, "
        "  odds_format, source, collected_at, source_confidence, is_verified, notes"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snap.snapshot_id,
            snap.match_id,
            snap.bookmaker,
            snap.market_type,
            snap.selection,
            None if snap.line is None else str(snap.line),
            str(snap.price),
            snap.odds_format,
            snap.metadata.source,
            snap.metadata.collected_at.isoformat(),
            snap.metadata.source_confidence,
            int(snap.metadata.is_verified),
            snap.metadata.notes,
        ),
    )
    conn.commit()


def _row_to_snapshot(row: sqlite3.Row) -> OddsSnapshot:
    return OddsSnapshot.model_validate(
        {
            "snapshot_id": row["snapshot_id"],
            "match_id": row["match_id"],
            "bookmaker": row["bookmaker"],
            "market_type": row["market_type"],
            "selection": row["selection"],
            "line": row["line"],
            "price": row["price"],
            "odds_format": row["odds_format"],
            "metadata": {
                "source": row["source"],
                "collected_at": row["collected_at"],
                "source_confidence": row["source_confidence"],
                "is_verified": bool(row["is_verified"]),
                "notes": row["notes"],
            },
        }
    )


def list_snapshots(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    market_type: str | None = None,
    bookmaker: str | None = None,
) -> list[OddsSnapshot]:
    """All snapshots for a match, ordered by collected_at ascending.

    Filters compose: pass ``market_type`` and/or ``bookmaker`` to narrow.
    """
    where = ["match_id = ?"]
    params: list = [match_id]
    if market_type is not None:
        where.append("market_type = ?")
        params.append(market_type)
    if bookmaker is not None:
        where.append("bookmaker = ?")
        params.append(bookmaker)
    rows = conn.execute(
        f"SELECT * FROM odds_snapshots WHERE {' AND '.join(where)} "
        "ORDER BY collected_at ASC",
        params,
    ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def latest_snapshot(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    market_type: str,
    bookmaker: str | None = None,
) -> OddsSnapshot | None:
    """Most recent snapshot for a (match, market) pair (and optional bookmaker)."""
    where = ["match_id = ?", "market_type = ?"]
    params: list = [match_id, market_type]
    if bookmaker is not None:
        where.append("bookmaker = ?")
        params.append(bookmaker)
    row = conn.execute(
        f"SELECT * FROM odds_snapshots WHERE {' AND '.join(where)} "
        "ORDER BY collected_at DESC LIMIT 1",
        params,
    ).fetchone()
    return _row_to_snapshot(row) if row else None


# Re-export SourceMetadata for downstream typing without re-importing models.
_ = SourceMetadata
