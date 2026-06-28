-- TIGER LINE PRIME V3.0 Sprint 4 — CLV records.
-- Applied when storage detects PRAGMA user_version < 3.

CREATE TABLE IF NOT EXISTS clv_records (
    clv_id           TEXT PRIMARY KEY,
    match_id         TEXT NOT NULL,
    bookmaker        TEXT NOT NULL,
    market_type      TEXT NOT NULL,
    selection        TEXT NOT NULL,
    entry_line       TEXT,
    entry_price      TEXT NOT NULL,
    entry_at         TEXT NOT NULL,
    closing_line     TEXT,
    closing_price    TEXT NOT NULL,
    closing_at       TEXT NOT NULL,
    clv_result       TEXT NOT NULL,                   -- beat | worse | flat
    clv_margin       TEXT NOT NULL,                   -- abs(line_delta) in line units
    price_delta      TEXT NOT NULL,                   -- closing_price - entry_price
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_clv_match ON clv_records(match_id);
CREATE INDEX IF NOT EXISTS idx_clv_created ON clv_records(created_at);

-- Append-only enforcement.
CREATE TRIGGER IF NOT EXISTS clv_records_no_update
    BEFORE UPDATE ON clv_records
BEGIN
    SELECT RAISE(ABORT, 'clv_records is append-only');
END;

CREATE TRIGGER IF NOT EXISTS clv_records_no_delete
    BEFORE DELETE ON clv_records
BEGIN
    SELECT RAISE(ABORT, 'clv_records is append-only');
END;
