# car16 — 車牌選號代辦平台

台灣車牌選號 / 標牌競標資料彙整 + 分級收費 + 委託代辦媒合。
零月費架構：Cloudflare Pages（前端）+ Workers（API + cron）+ D1（資料庫）。

## 快速開始（本機）

```bash
make test            # Worker 純函式測試（ecpay 簽章 / normalize / gating）
make build           # 打包前端 → dist/site/
make db-local        # 本地 D1 跑 migrations（需 wrangler）
make db-seed-local   # 灌開發假資料
make dev             # 本機跑 API（http://localhost:8787）
make serve           # 本機預覽前端（http://127.0.0.1:8788）
```

前端要連本機 API 時，在瀏覽器 console 執行一次：
`localStorage.setItem('car16_api_base', 'http://localhost:8787')`

## 首次部署（一次性）

1. `make db-create` → 把回傳的 `database_id` 填進 `worker/wrangler.toml`
2. `make db-migrate`（遠端 D1 跑 migrations + 方案 seed）
3. `make deploy-worker` → 記下 Worker 網址，填進 `worker/wrangler.toml` 的 `WORKER_ORIGIN` var 與 `site/js/config.js` 的 `API_BASE`
4. `wrangler secret put INGEST_TOKEN`（自訂一組長亂數）
5. `make deploy` → 前端上線 `https://car16.pages.dev`
6. **校準 mvdis 直抓**：`curl -H "Authorization: Bearer $INGEST_TOKEN" https://<worker>/api/ingest/probe`
   - 通 → 依實際 DOM 校準 `worker/src/ingest/sources/mvdis.js`
   - 不通 → 啟用備援：`scripts/ingest-fallback.mjs`（本機 cron 或 GitHub Actions，見 docs/OPS.md）

## 收費切正式（綠界）

見 `docs/ECPAY.md`。開發/測試用綠界 stage 公開測試商店（2000132），
正式上線：`wrangler secret put ECPAY_MERCHANT_ID / ECPAY_HASH_KEY / ECPAY_HASH_IV` + `ECPAY_ENV=production`。

## 目錄

- `site/` 前端（純靜態、手刻 HTML/JS）
- `worker/` API Worker（路由 `src/routes/`、攝取 `src/ingest/`、金流 `src/ecpay.js`）
- `migrations/` D1 schema
- `scripts/` 備援攝取推送器、開發 seed
- `docs/` 架構 / 金流 / 維運

## 定價改數字

`migrations` seed 了三個方案（149/499/1999）。上線後改價：
`wrangler d1 execute car16 --remote --command "UPDATE plans SET price_twd=299 WHERE id='consumer'"`
（前端 `index.html` 定價卡與 `worker/src/routes/chat.js` FAQ 文案要同步改。）
