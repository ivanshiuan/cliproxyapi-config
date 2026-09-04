# 架構

```
瀏覽器 ── Pages (car16.pages.dev, 靜態) ──┐
   │                                      │ fetch (Bearer token)
   │                                      ▼
   │                          Worker car16-api（單一入口 src/index.js）
   │                            ├─ routes/plates    查詢/公告（免費配額 gating）
   │                            ├─ routes/account   註冊/登入/me/watchlist
   │                            ├─ routes/billing   綠界結帳 + webhook
   │                            ├─ routes/agency    代辦 leads
   │                            ├─ routes/chat      客服（規則式 / Claude）
   │                            ├─ routes/ingest    備援推送 + probe
   │                            └─ scheduled()      cron 直抓 mvdis
   │                                      │
   └── 綠界 AioCheckOut（POST 表單）       ▼
        └→ ReturnURL webhook ────────▶  D1 (SQLite)
```

## 資料流（攝取）

主路徑：Worker cron（台灣 06:00–19:00 每 30 分）→ `ingest/sources/mvdis.js` 直抓
→ `normalize.js` → upsert `plate_cache` / `auction_announcements` → `ingest_runs` 記錄。

備援路徑：`scripts/ingest-fallback.mjs`（本機/GitHub Actions IP）抓同樣頁面
→ POST `/api/ingest`（Bearer INGEST_TOKEN）→ **同一套** normalize + upsert。

兩路徑寫入同表，`freshness()` 以最後一次成功攝取時間算 staleness（>3h 顯示警示）。

## 金流狀態機

```
checkout → payments(pending) → 綠界頁
  ├─ 信用卡成功 → webhook RtnCode=1 → paid → activateSubscription()
  ├─ ATM 取號  → webhook RtnCode=2 → pending + 虛擬帳號 → (1-3天) RtnCode=1 → paid
  └─ 失敗      → webhook 其他碼   → failed
```

- 開通只信 webhook（`/api/ecpay/return` 驗 CheckMacValue），前端結果頁純顯示。
- 冪等：payment 已 paid 就不重複開通；同 plan 未到期 → 展延 30 天。

## Gating

`entitlements.js` 是唯一真相：free（每日 10 查詢 / 匿名 3）、consumer/pro/dealer
（features_json 存 DB plans 表）。配額計數在 D1 `usage_counters`（台北時區日界線）。
前端 `GET /api/me` 只拿來畫鎖頭，不做真正限制。

## 免費層額度對照

| 資源 | 免費層 | 本設計用量 |
|---|---|---|
| Workers 請求 | 100k/日 | MVP 流量遠低於此 |
| Workers CPU | 10ms（p99）| PBKDF2 10 萬迭代僅登入/註冊觸發，可調 |
| D1 讀 | 5M rows/日 | 查詢頁主耗，加了索引 |
| D1 寫 | 100k/日 | 攝取 upsert + 配額計數，分批 |
| cron | 5 個排程 | 用 2 個 |
