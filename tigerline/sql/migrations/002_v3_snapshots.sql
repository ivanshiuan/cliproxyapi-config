-- TIGER LINE PRIME V3.0 Sprint 1 — append-only odds snapshots.
-- Applied when storage detects PRAGMA user_version < 2.
-- Re-runnable: every CREATE uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    match_id        TEXT NOT NULL,
    bookmaker       TEXT NOT NULL,
    market_type     TEXT NOT NULL,
    selection       TEXT NOT NULL,
    line            TEXT,                              -- nullable for moneyline / BTTS
    price           TEXT NOT NULL,                     -- Decimal as TEXT
    odds_format     TEXT NOT NULL,
    source          TEXT NOT NULL,                     -- manual | api | screenshot | system
    collected_at    TEXT NOT NULL,                     -- ISO-8601 UTC
    source_confidence TEXT NOT NULL,                   -- high | medium | low
    is_verified     INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    inserted_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only enforcement: prevent UPDATE / DELETE.
-- Same pattern as restaurant_api ledger tables.
CREATE TRIGGER IF NOT EXISTS odds_snapshots_no_update
    BEFORE UPDATE ON odds_snapshots
BEGIN
    SELECT RAISE(ABORT, 'odds_snapshots is append-only');
END;

CREATE TRIGGER IF NOT EXISTS odds_snapshots_no_delete
    BEFORE DELETE ON odds_snapshots
BEGIN
    SELECT RAISE(ABORT, 'odds_snapshots is append-only');
END;

-- Time-series access patterns: by match (movement), by collected_at (CLV),
-- by bookmaker (consensus).
CREATE INDEX IF NOT EXISTS idx_snapshots_match_time
    ON odds_snapshots(match_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_book
    ON odds_snapshots(bookmaker, collected_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_market
    ON odds_snapshots(match_id, market_type, collected_at);
