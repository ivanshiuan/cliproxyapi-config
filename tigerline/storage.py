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
    ReviewResult,
)

SCHEMA_VERSION = 1


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
    sql = _init_sql_path().read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
