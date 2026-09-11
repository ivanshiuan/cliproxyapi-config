# 維運手冊

## Secrets 清單

| Secret | 用途 | 必要性 |
|---|---|---|
| `INGEST_TOKEN` | 備援推送 / probe 的 Bearer token | 必要（未設則 /api/ingest 關閉） |
| `ECPAY_*` 三件組 | 正式金流 | 上線收費前 |
| `ANTHROPIC_API_KEY` | 客服機器人升級 Claude AI | 可選（未設走規則式 FAQ） |

## mvdis 直抓校準（部署後第一件事）

```bash
curl -H "Authorization: Bearer $INGEST_TOKEN" https://<worker>/api/ingest/probe | python3 -m json.tool
```

- 三個端點 `status: 200` 且 `platesFound` 非空 → 直抓可用；依 `sample` 的實際 DOM
  校準 `worker/src/ingest/sources/mvdis.js`（deptCode、表格欄位 → 補齊底價/日期解析）
- `403/443/超時` → Cloudflare IP 被擋，啟用備援（見下）

## 備援攝取（GitHub Actions 每 30 分鐘）

`.github/workflows/ingest.yml`（需要時再加，內容如下）：

```yaml
name: ingest-fallback
on:
  schedule: [{ cron: "*/30 22-23,0-11 * * *" }]  # 台灣白天
  workflow_dispatch: {}
jobs:
  push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: node car16/scripts/ingest-fallback.mjs
        env:
          API_BASE: ${{ vars.CAR16_API_BASE }}
          INGEST_TOKEN: ${{ secrets.CAR16_INGEST_TOKEN }}
```

GitHub IP 也被擋的最後手段：本機用 browser-act 抓（真瀏覽器），
產出 rows.json 後 `node scripts/ingest-fallback.mjs --file rows.json --kind auction`。

## 日常監控

- 攝取健康：`GET /api/ingest/status`（公開，最近 20 筆 runs）
- 前端查詢頁的資料時間 banner >3 小時會轉黃
- 新代辦 leads：`wrangler d1 execute car16 --remote --command "SELECT * FROM agency_requests WHERE status='new' ORDER BY created_at DESC"`
- 付款查帳：綠界後台為準，對 `payments` 表核對

## 過期清理（量大再做）

sessions / usage_counters 會累積。免費層 500MB 撐很久；需要時加一個 cron
`DELETE FROM sessions WHERE expires_at < datetime('now','-7 days')`，
`DELETE FROM usage_counters WHERE key LIKE 'q:%' AND key < 'q:...30天前'`。

## 網域切換 car16.com

Pages 專案 → Custom domains 加 `car16.com` / `www.car16.com`；
Worker 加 route `api.car16.com/*`，然後：
1. `wrangler.toml` vars：`SITE_ORIGIN=https://car16.com`、`WORKER_ORIGIN=https://api.car16.com`
2. `site/js/config.js`：`API_BASE` 改 `https://api.car16.com`
3. 重新 `make deploy deploy-worker`
