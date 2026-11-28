-- car16 v1 初始 schema（Cloudflare D1 / SQLite）
-- 慣例：時間一律存 UTC ISO8601 文字（datetime('now')），前端轉台北時間顯示。

-- ── 用戶與 session ────────────────────────────────────────────────
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,              -- pbkdf2$<iter>$<salt_b64>$<hash_b64>
  display_name  TEXT,
  phone         TEXT,
  role          TEXT NOT NULL DEFAULT 'user',   -- user | admin
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
  token      TEXT PRIMARY KEY,              -- 32-byte random hex
  user_id    INTEGER NOT NULL REFERENCES users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

-- ── 方案與訂閱（v1 = 30 天通行證，非定期定額）──────────────────────
CREATE TABLE plans (
  id            TEXT PRIMARY KEY,           -- consumer | pro | dealer
  name          TEXT NOT NULL,
  price_twd     INTEGER NOT NULL,
  duration_days INTEGER NOT NULL DEFAULT 30,
  features_json TEXT NOT NULL
);

CREATE TABLE subscriptions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  plan_id    TEXT NOT NULL REFERENCES plans(id),
  starts_at  TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  payment_id INTEGER,
  status     TEXT NOT NULL DEFAULT 'active' -- active | expired
);
CREATE INDEX idx_subs_user ON subscriptions(user_id, expires_at);

CREATE TABLE payments (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_trade_no TEXT NOT NULL UNIQUE,   -- ≤20 字，C16 + base36 時間 + 亂數
  user_id           INTEGER NOT NULL REFERENCES users(id),
  plan_id           TEXT NOT NULL,
  amount_twd        INTEGER NOT NULL,
  method            TEXT,                   -- Credit | ATM
  status            TEXT NOT NULL DEFAULT 'pending', -- pending | paid | failed | expired
  ecpay_trade_no    TEXT,
  atm_bank_code     TEXT,
  atm_v_account     TEXT,
  atm_expire_date   TEXT,
  raw_return_json   TEXT,                   -- 最近一次 webhook 原始 payload（除錯）
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  paid_at           TEXT
);
CREATE INDEX idx_payments_user ON payments(user_id, created_at);

-- ── 車牌資料快取（來源：監理服務網公開資料）────────────────────────
CREATE TABLE plate_cache (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  region       TEXT NOT NULL,               -- 監理所/站名稱
  plate_no     TEXT NOT NULL,
  vehicle_type TEXT NOT NULL,               -- 自用小客車 / 重型機車 …
  plate_prefix TEXT,                        -- 字軌，如 BXX
  status       TEXT NOT NULL DEFAULT 'available',
  raw_json     TEXT,
  fetched_at   TEXT NOT NULL,
  UNIQUE (region, vehicle_type, plate_no)
);
CREATE INDEX idx_plate_no ON plate_cache(plate_no);
CREATE INDEX idx_plate_region ON plate_cache(region, vehicle_type);

CREATE TABLE auction_announcements (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  region          TEXT NOT NULL,
  plate_no        TEXT NOT NULL,
  vehicle_type    TEXT,
  base_price_twd  INTEGER,
  current_bid_twd INTEGER,
  bid_count       INTEGER,
  announce_start  TEXT,
  bid_start       TEXT,
  bid_end         TEXT,
  phase           TEXT NOT NULL,            -- announced | bidding | closed
  raw_json        TEXT,
  fetched_at      TEXT NOT NULL,
  UNIQUE (region, plate_no, bid_start)
);
CREATE INDEX idx_auction_phase ON auction_announcements(phase, bid_end);

CREATE TABLE ingest_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source        TEXT NOT NULL,              -- mvdis-direct | external-push
  kind          TEXT NOT NULL,              -- pickno | auction | bidding
  ok            INTEGER NOT NULL,
  rows_upserted INTEGER,
  error         TEXT,
  ran_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 追蹤清單 / 代辦委託 / 客服 ────────────────────────────────────
CREATE TABLE watchlists (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL REFERENCES users(id),
  pattern      TEXT NOT NULL,               -- 完整號碼或含 * 萬用字元樣式
  vehicle_type TEXT,
  region       TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, pattern, vehicle_type, region)
);

CREATE TABLE agency_requests (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id          INTEGER REFERENCES users(id),  -- 允許未登入留單
  kind             TEXT NOT NULL,           -- pickno | bid | support
  contact_name     TEXT NOT NULL,
  contact_phone    TEXT NOT NULL,
  contact_line     TEXT,
  plate_pattern    TEXT,
  region           TEXT,
  vehicle_type     TEXT,
  budget_twd       INTEGER,
  note             TEXT,
  status           TEXT NOT NULL DEFAULT 'new', -- new | contacted | assigned | done | dropped
  assigned_partner TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_agency_status ON agency_requests(status, created_at);

CREATE TABLE chat_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_key TEXT NOT NULL,                -- 前端匿名 UUID
  user_id     INTEGER,
  role        TEXT NOT NULL,                -- user | bot
  message     TEXT NOT NULL,
  intent      TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_chat_session ON chat_logs(session_key, created_at);

-- ── 免費層配額 / 通用計數器（不用 KV：KV 免費層每日僅 1,000 寫入）──
CREATE TABLE usage_counters (
  key   TEXT PRIMARY KEY,                   -- 'q:<user|ip>:<yyyy-mm-dd>' / 'login:<ip>:<hour>' / 游標
  count INTEGER NOT NULL DEFAULT 0
);

-- ── 方案 seed（定價可改：UPDATE plans SET price_twd=…）────────────
INSERT INTO plans (id, name, price_twd, duration_days, features_json) VALUES
  ('consumer', '一般版',     149,  30, '{"daily_queries":-1,"results_cap":-1,"watchlist":10,"export":false,"batch":false,"priority_agency":false}'),
  ('pro',      '業務專業版', 499,  30, '{"daily_queries":-1,"results_cap":-1,"watchlist":50,"export":true,"batch":true,"priority_agency":false}'),
  ('dealer',   '車商版',     1999, 30, '{"daily_queries":-1,"results_cap":-1,"watchlist":200,"export":true,"batch":true,"priority_agency":true}');
