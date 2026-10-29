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
3. 起 **PostgreSQL 16 + pgvector** 服務：用跟 `docker-compose.production.yml` 同一個映像 `pgvector/pgvector:pg16`
   （遷移會 `CREATE EXTENSION vector`，沒有 pgvector 的 Postgres 會在 `alembic upgrade head` 失敗；
   若改用 Zeabur 內建的 Postgres，先確認它有 pgvector）。
   連上去先建三個擴充：`uuid-ossp`、`citext`、`pg_trgm`（內容同 `restaurant_api/initdb/01_extensions.sql`；
   compose 靠 initdb 自動做，Zeabur 要手動做一次）。記下連線資訊（host / port / user / password / dbname）。
4. 部署 **restaurant_api**（來源：GitHub repo 的預設分支）：
   - **用 repo 的 Dockerfile，但它不在根目錄**：服務環境變數加
     `ZBPACK_DOCKERFILE_PATH=restaurant_api/Dockerfile`（Zeabur 官方文件：子目錄的 Dockerfile 用這個變數，路徑相對於 repo 根目錄；
     見 <https://zeabur.com/docs/en-US/deploy/methods/dockerfile>）。建置 context 必須是 repo 根目錄，
     因為 Dockerfile 會 `COPY` 根目錄的 `devswarm/`、`scripts/`、`pyproject.toml`。
   - **資料庫變數是 `RESTO_DB_HOST` / `RESTO_DB_PORT` / `RESTO_DB_USER` / `RESTO_DB_PASSWORD` / `RESTO_DB_NAME`**
     （程式不讀 `DATABASE_URL`，設了也沒用；Zeabur Postgres 給的變數要對映到這五個）。
   - **必須一併注入** `RESTO_ENV=prod`、`RESTO_ADMIN_PASSCODE`（至少 8 字元）、`RESTO_SESSION_SECRET`
     （`openssl rand -hex 32`）：預設值在公開 repo 裡，api 看到預設或太弱的值會**拒絕啟動**。
     通行碼是老闆登入後台要用的，產生後**只在對話裡告訴老闆一次**，不要寫進任何檔案或 git。
   - 損益報表要的金額（`RESTO_PNL_FIXED_COSTS_MONTHLY`、`RESTO_PNL_LABOR_ON_COST_RATE`，見 `docs/DAILY_PNL.md`）問老闆再設，不要猜。
   - **不需要 Redis**（程式沒有用到，只有設定欄位）。LINE 沒設 token 就是 Stub，不會真的推播。
   - 服務埠 8000，健康檢查路徑 `/health/live`。`.env` 絕不進 git。
   - **另起第二個服務跑排程**：同一個映像、啟動指令 `python -m restaurant_api.jobs`、同一組 `RESTO_DB_*`，**只開 1 個副本**
     （排程在 process 內，開兩個會重複執行）。
5. 在 api 容器內跑 `cd restaurant_api && alembic upgrade head` 建全部資料表（數量見 `docs/STATUS.md`）；
   跑完確認 `alembic heads` 只有一個、且 `alembic current` 等於它。
6. **示範資料：先問老闆，不要自己決定。** `python scripts/seed_demo_data.py`（在 api 容器內跑，映像裡有 `scripts/`）會灌
   1 間店、5 個員工、12 道菜、3 個假顧客（其中 1 個綁了假的 LINE）、一個開著的錢箱。
   要拿這個環境真的營業就**不要灌**：會污染員工名單、菜單與每日損益；只想讓老闆點點看，就另開一個試玩環境。
7. 綁定網域：預設先給 `https://<project>.zeabur.app`（後台登入 cookie 在正式模式帶 `Secure`，必須走 HTTPS）；
   若有 `CLOUDFLARE_API_TOKEN` ＋ Ivan 指定的網域（如 `pos.xxx.tw`），加 CNAME 指到 Zeabur 並在 Zeabur 綁定。
8. 驗收：對線上網址跑冒煙測試（/health/ready、用新通行碼登入 `/demo/admin.html`、舊的預設通行碼必須被拒、
   開桌點餐結帳一輪、`/reports/daily-pnl`、六個前台頁面 Playwright 各開一次零 console 錯誤），把**全部頁面網址清單**回報給 Ivan：
   - `/pos/?store=…` 店員平板、`/order/?t=…` 掃碼點餐、`/takeout/?store=…` 線上外帶、
     `/kds/?store=…` 廚房、`/pickup-board/?store=…` 取餐看板、`/book/?store=…` 訂位、
     `/hq/` 總部儀表板
9. 上線後保養：DB 每日備份設定 + `/health/ready` 監控（照 docs/11 §4-5 簡化版）。

## 4. 風險與注意

- **正式營業前**仍需：金流/發票憑證（docs/22 🔴 清單）、`.env.production` 密碼
  管理（docs/11 §2）、以及把 demo seed 資料清掉換成真實菜單。
- token 若曾貼在對話裡，部署完建議撤銷重簽，改放環境變數。
- 沙盒 egress 白名單是安全機制：只開上面列的網域，不建議開 Full。
- 上面 Zeabur 的具體做法（子目錄 Dockerfile 變數、服務拆法）是依 Zeabur 官方文件整理，**還沒有在真的 Zeabur 帳號上實測過**；
  第一次部署若與實際畫面或 API 有出入，以實際為準，並回頭修正這份文件。
