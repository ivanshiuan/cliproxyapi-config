-- TIGER LINE PRIME V2.0 — SQLite schema
-- Apply once on a fresh db; storage.init() bumps PRAGMA user_version after.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS matches (
    match_id     TEXT PRIMARY KEY,
    kickoff_utc  TEXT NOT NULL,
    home         TEXT NOT NULL,
    away         TEXT NOT NULL,
    input_json   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS classifications (
    match_id     TEXT NOT NULL REFERENCES matches(match_id),
    scenario     TEXT NOT NULL,
    confidence   TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (match_id, created_at)
);

CREATE TABLE IF NOT EXISTS recommendations (
    match_id     TEXT NOT NULL REFERENCES matches(match_id),
    plan_json    TEXT NOT NULL,
    bankroll     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (match_id, created_at)
);

CREATE TABLE IF NOT EXISTS results (
    match_id     TEXT PRIMARY KEY REFERENCES matches(match_id),
    home_goals   INTEGER NOT NULL,
    away_goals   INTEGER NOT NULL,
    review_json  TEXT NOT NULL,
    reviewed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rule_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     TEXT NOT NULL REFERENCES matches(match_id),
    suggestion   TEXT NOT NULL,
    applied      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_results_reviewed ON results(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_classifications_scenario ON classifications(scenario);
