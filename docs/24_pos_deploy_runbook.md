# 24 — POS 上線 Runbook（Zeabur 主機 + Cloudflare 門面）

> 目的：把 restaurant_api 整套 POS 變成「Ivan 點一個網址就能用」的線上系統。
> 建立：2026-07-15。背景：Ivan 要求直接上線；本環境實測 egress 白名單擋掉
> Cloudflare/Zeabur 等所有部署 API，需 Ivan 先開通（见 §2）。

---

## 1. 為什麼不是「像之前一樣丟 Cloudflare Pages」

| | 之前的網站（498-win 等） | 這套 POS |
|---|---|---|
| 體質 | 純靜態網頁 | FastAPI 伺服器 + PostgreSQL 資料庫 + WebSocket |
| Cloudflare Pages 放得下嗎 | ✅ | ❌（Pages 只能放靜態檔，沒有資料庫） |
| 正確位置 | Pages | **Zeabur**（跑 API + PG 容器）＋ Cloudflare 只當 DNS/門面 |

Zeabur 是首選因為：這個 repo 本來就是 Zeabur 配置、有現成 `restaurant_api/Dockerfile`
（multi-stage、non-root、tini）、Zeabur 一鍵給 PostgreSQL 與 `*.zeabur.app` 網址、
之後 Cloudflare 網域 CNAME 過去即可。單店規模（日 <5,000 單）綽綽有餘；
放大之後再照 `docs/11` 遷去 VM。

## 2. Ivan 要做的三步（一次性，約 5 分鐘）

1. **開網路白名單**（跟當初開 browser-act 同一個地方，見 docs/18）：
   claude.ai/code → 雲朵圖示 → 環境那列的齒輪 → **Network access → Custom** →
   Allowed domains 加：
   ```
   zeabur.com
   *.zeabur.com
   api.zeabur.com
   zeabur.app
   *.zeabur.app
   api.cloudflare.com
   *.cloudflare.com
   ```
   勾「Also include default list of common package managers」→ 存檔。
2. **加環境變數**（同一個設定視窗 → Environment variables）：
   - `ZEABUR_API_KEY` — Zeabur 後台 → 頭像 → Settings → API Key 產生
   - `CLOUDFLARE_API_TOKEN` —（選填，綁自訂網域才要）Cloudflare 後台 →
     My Profile → API Tokens → 建一把只有「Zone → DNS → Edit」權限的 token
3. **開一個新的 cloud session**（設定只在 session 啟動時生效），對 Claude 說
   「**部署 POS**」。

## 3. Claude 收到「部署 POS」後做的事（自動）

1. `bash scripts/deploy_preflight.sh` — 確認 token 與 egress 全綠，缺什麼直接印出指引。
2. Zeabur 建專案：`restaurant-pos`（region 選 HKG/TPE 就近）。
3. 起 **PostgreSQL 16** 服務 → 取得連線字串。
4. 部署 **restaurant_api**（用 repo 的 Dockerfile；`DATABASE_URL` 等環境變數注入；
   `.env` 絕不進 git）。
5. 跑 `alembic upgrade head` 建全部 41 張表。
6. `make seed` 灌示範餐廳資料（菜單/桌位/員工），讓 Ivan 點進去就有東西可玩。
7. 綁定網域：預設先給 `https://<project>.zeabur.app`；若有 `CLOUDFLARE_API_TOKEN`
   ＋ Ivan 指定的網域（如 `pos.xxx.tw`），加 CNAME 指到 Zeabur 並在 Zeabur 綁定。
8. 驗收：對線上網址跑冒煙測試（/health/ready、開桌點餐結帳一輪、六個前台頁面
   Playwright 各開一次零 console 錯誤），把**全部頁面網址清單**回報給 Ivan：
   - `/pos/?store=…` 店員平板、`/order/?t=…` 掃碼點餐、`/takeout/?store=…` 線上外帶、
     `/kds/?store=…` 廚房、`/pickup-board/?store=…` 取餐看板、`/book/?store=…` 訂位、
     `/hq/` 總部儀表板
9. 上線後保養：DB 每日備份設定 + `/health/ready` 監控（照 docs/11 §4-5 簡化版）。

## 4. 風險與注意

- **正式營業前**仍需：金流/發票憑證（docs/22 🔴 清單）、`.env.production` 密碼
  管理（docs/11 §2）、以及把 demo seed 資料清掉換成真實菜單。
- token 若曾貼在對話裡，部署完建議撤銷重簽，改放環境變數。
- 沙盒 egress 白名單是安全機制：只開上面列的網域，不建議開 Full。
