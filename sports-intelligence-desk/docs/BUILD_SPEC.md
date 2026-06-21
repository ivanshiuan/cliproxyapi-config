# BUILD SPEC — Sports Intelligence Desk (SID)
### 可交付給任意 AI Agent 直接執行的「建置 / 測試 / 部署」完整規格

> **文件性質**：這是一份 self-contained 的執行手冊。拿到它的 AI / 工程師
> 不需要任何額外上下文，照本文件即可從零 **建置 → 驗證 → 部署上線**。
> **專案代號**：SID（Sports Intelligence Desk，賽事投研終端）
> **產物**：一個純前端（vanilla JS、零執行期依賴）的單頁應用 + 可選的 Cloudflare Worker CORS 代理。
> **最終上線目標**：Cloudflare Pages 獨立專案，網址 `https://<project>.pages.dev`。

---

## 0. TL;DR（給趕時間的 Agent）

```bash
# 1) 取得程式碼
git clone <REPO_URL>
cd <repo>/sports-intelligence-desk
git checkout <branch>          # 本專案開發分支：claude/sports-intelligence-desk-dayved

# 2) 驗證（零依賴，需 Node ≥ 18）
node qa.js            # 必須印出 "PASS 1490 / FAIL 0"
node backtest_sim.js  # 可複現回測報告

# 3) 建置
node build.js         # 產出 dist/SID-standalone.html（單檔）+ dist/site/（部署根目錄）

# 4) 部署到 Cloudflare Pages（擇一，見 §6）
CLOUDFLARE_API_TOKEN=<token> npx wrangler@latest pages deploy dist/site --project-name <project>
```

**驗收**：開 `https://<project>.pages.dev`，看到「MATCH INTELLIGENCE TERMINAL」四分頁（主控台 / 四場組合 / 時間軸 / 設定），數字非 NaN，即成功。

---

## 1. 專案是什麼

把每一場球賽當作「即將開盤的資產」，用多因子量化模型輸出
**真實機率 vs 市場隱含機率的落差（Edge / Alpha）**，而非單純預測勝負。

- **輸入**：球隊基本面（Elo / xG / 近況 / 陣容 / 戰術風格）、賽事環境、莊家賠率。
- **處理**：8 個子模型 pipeline + Dixon-Coles 比分矩陣 + 溫度校準 + 蒙地卡羅不確定度 + 模型/市場集成。
- **輸出**：每場 1X2 / 大小分 / BTTS / 正確比分機率、評級 S/A/B/C/X、信心分、四場組合分、投研 Memo。

---

## 2. 技術棧與硬限制

| 項目 | 規格 |
|---|---|
| 前端 | 原生 HTML/CSS/JavaScript（IIFE 模組，掛在 `window.SID`），**無框架、無打包器、無執行期依賴** |
| 建置 | 單一 `build.js`（Node 內建 `fs`/`path`，**零 npm 依賴**），把多檔內嵌成單檔 + 組乾淨部署目錄 |
| 測試 | `qa.js`（Node + DOM shim 跑 10 輪復盤）、`backtest_sim.js`（5000 場合成回測） |
| 執行環境 | 任意現代瀏覽器；建置/測試需 **Node ≥ 18**（用到 `fs.cpSync`/`fs.rmSync`） |
| 部署 | Cloudflare Pages（靜態）；可選 Cloudflare Worker 當 Live API 的 CORS 代理 |

> **鐵律（不可違反）**：
> 1. 所有機率必須正規化（加總 = 1，誤差 < 1e-6）。
> 2. **不得捏造未查證的傷病/先發**；球員資料只能來自查證快照或官方 API。
> 3. 金額/機率計算保持確定性；蒙地卡羅以 mean-preserving 抽樣，不得偏離解析期望。

---

## 3. 檔案結構（部署相關）

```
sports-intelligence-desk/
├── index.html                 # 入口（依序載入 6 支 js）
├── manifest.webmanifest       # PWA manifest
├── sw.js                      # service worker（離線快取；file:// 下自動略過）
├── css/terminal.css           # 終端風格樣式
├── js/
│   ├── data.js      # 資料層：teams / players / matches / odds / results / meta
│   ├── history.js   # 歷史交手 / 大賽戰績
│   ├── engine.js    # 核心引擎：M1–M8 + 蒙地卡羅 + 戰術量化 + 集成
│   ├── backtest.js  # Brier / LogLoss / 可靠度 / 基準比較
│   ├── live.js      # 3 家賽事 API 對接 + 傷病同步 + 自動降級
│   └── app.js       # 路由 / 渲染 / Memo / 設定頁（並導出 SID._test 供 QA）
├── build.js         # 建置腳本 → dist/
├── qa.js            # 上線前 10 輪復盤（驗收門檻）
├── backtest_sim.js  # 合成回測
├── Makefile         # build / serve / qa / backtest / deploy-cf
├── deploy/          # 可選：Cloudflare Worker（CORS 代理）+ wrangler.toml
└── docs/            # 文件（本檔、部署、Live、回測報告、HANDOFF）
```

**建置產物（gitignore，不進版控）**：
```
dist/
├── SID-standalone.html   # 全內嵌單檔，雙擊即開、離線可跑（給人預覽/分享）
└── site/                 # Cloudflare Pages 上傳根目錄（只含執行檔，無測試碼）
    ├── index.html  manifest.webmanifest  sw.js
    ├── css/terminal.css
    └── js/{data,history,engine,backtest,live,app}.js
```

---

## 4. 建置流程（build.js 行為契約）

執行 `node build.js`（cwd 任意；腳本以 `__dirname` 定位），必須產生：

1. **`dist/SID-standalone.html`**
   - 把 `css/terminal.css` 內嵌成 `<style>`
   - 移除 `<link rel="manifest">` 與 service worker 註冊
   - 把 6 支 js 依序（data→history→engine→backtest→live→app）內嵌成單一 `<script>`
   - **健檢**：產物內不得殘留任何 `<script src=` 或 `<link `（違反則 `exit 1`）

2. **`dist/site/`**（部署根目錄）
   - 複製 `index.html`、`manifest.webmanifest`、`sw.js`、`css/`、`js/`
   - **刻意排除** `qa.js` / `backtest_sim.js` / `build.js` / `docs/` / `deploy/`（避免測試碼外洩）

預期輸出：
```
✓ dist/SID-standalone.html (~87 KB) — 單檔離線可開
✓ dist/site/ (10 檔) — Cloudflare Pages 上傳根目錄
```

---

## 5. 測試 / 驗收門檻（部署前必過）

```bash
node qa.js            # 10 輪復盤，必須： "===== 總計 PASS 1490 / FAIL 0 ====="
node qa.js 30         # 壓測 30 輪（抓隨機性/狀態污染），FAIL 必須 = 0
node backtest_sim.js  # 5000 場合成回測，可複現
```

`qa.js` 覆蓋 10 個面向（每輪 149 斷言）：
1. 模組/導出存在　2. 數學正規化（1X2、比分矩陣、Poisson 和=1、大小分單調）
3. 蒙地卡羅（區間含點估計、≈解析期望）　4. 回測（Brier 自檢、命中判定）
5. 資料健全（Elo/xG/賠率抽水合理、無捏造傷病）　6. 時區（UTC→Asia/Taipei）
7. UI 四分頁 + 單場頁 + Memo 渲染無 undefined/NaN　8. 邊界（極端錯配、缺球員、極小 λ）
9. Live 降級 + 傷病同步（只動 curated 名單、不造假）　10. 端到端評級/組合/集成

**任一 FAIL 不得部署。**

---

## 6. 部署（Cloudflare Pages）— 三法擇一

> 每個 Pages「專案」彼此完全隔離（獨立子網域/設定/部署）。
> **要與既有專案無關，就新建一個專案、取一個不同的名字即可。**

### 方法 A — Dashboard 直接上傳（零 CLI，最快）
1. Cloudflare → **Workers & Pages** → **Create** → **Pages** → **Upload assets**
2. 專案名輸入 `<project>`（決定網址）
3. 把 `dist/site/` 整包拖入 → **Deploy**
4. 完成 → `https://<project>.pages.dev`
> 也可直接拖 `dist/SID-standalone.html`，但需先改名為 `index.html`。

### 方法 B — wrangler CLI（一行）
```bash
cd sports-intelligence-desk    # 確保 dist/site 相對路徑正確（可複製貼上）
node build.js
export CLOUDFLARE_API_TOKEN=<具 Pages:Edit 權限的 token>
npx wrangler@latest pages deploy dist/site --project-name <project> --commit-dirty=true
```
**前置條件（缺一不可）**：
- 環境變數 `CLOUDFLARE_API_TOKEN`（用「Cloudflare Pages: Edit」模板建立）
- 執行環境的**對外網路必須允許 `api.cloudflare.com`**（沙盒/受限網路常見 403：
  `Host not in allowlist: api.cloudflare.com` → 需把該 host 加入 egress 白名單）

### 方法 C — 連 Git 自動部署（之後 push 自動上線，推薦長期用）
Cloudflare → Pages → **Connect to Git** → 選倉庫 → 設定：

| 欄位 | 值 |
|---|---|
| Production branch | `<branch>`（本專案：`claude/sports-intelligence-desk-dayved`，或合併後的 main） |
| Framework preset | `None` |
| Root directory (Advanced) | `sports-intelligence-desk` |
| Build command | `node build.js` |
| Build output directory | `dist/site` |

→ **Save and Deploy**。Cloudflare 會自行 clone + 跑 `node build.js`（零依賴，必成功）。

---

## 7. 可選：Live 即時資料 + CORS 代理 Worker

App 本體純靜態，**不接 API 也能跑**（用內建查證快照）。若要 Live 抓賽程/比分/傷病：

1. 部署 `deploy/worker.js`（取**新** worker 名，與其他專案無關）：
   ```bash
   cd deploy
   npx wrangler@latest deploy
   npx wrangler@latest secret put APIFOOTBALL_KEY   # 貼 key，前端永不可見
   ```
2. App → 設定頁 → provider 選 `API-Football`、proxy 填 `https://<worker>.workers.dev/?u=` → 「測試連線並更新」。

> `live.js` 支援 API-Football / football-data.org / TheSportsDB；抓不到自動降級用快照。
> 傷病同步**只比對 curated 名單既有球員（依姓氏）**，不無中生有、不造假。

---

## 8. 誠實揭露（交付對象必須知道，勿當成 bug）

- **Edge 數字不是獲利保證**：合成回測中模型聲稱的 1X2 edge 資訊量 < 50%。
- **純模型未穩定優於市場**；最可信輸出是 **A5 集成（模型 40% + 市場 60%）**。
- **評級衡量「價差」不是「準度」**：高評級場次的 Brier 反而可能較高（高 edge 落在更難測的區間），屬設計取捨。
- **真實 Alpha 需實戰累積**：把賽後比分填入 `data.js` 的 `SID.results`，`backtest.js` 才能算真實命中率。

---

## 9. 排錯

| 症狀 | 原因 / 解法 |
|---|---|
| `Host not in allowlist: api.cloudflare.com … 403` | 執行環境 egress 沒開 Cloudflare → 改用方法 A/C，或把 `api.cloudflare.com` 加進白名單 |
| wrangler `necessary to set a CLOUDFLARE_API_TOKEN` | 未設 token → `export CLOUDFLARE_API_TOKEN=...`（Pages:Edit 權限） |
| 專案名被改寫 | Cloudflare 只收小寫/數字/連字號，空格與大寫會被正規化（如 `498 WIN` → `498-win`） |
| build 報錯 `fs.cpSync is not a function` | Node 太舊 → 升到 Node ≥ 18 |
| QA 出現 FAIL | **禁止部署**；先修到 1490/1490 全綠 |
| 頁面空白/CORS 報錯 | 用 `http://`（`python3 -m http.server` 或 Pages）而非 `file://` 開（service worker 才會註冊） |

---

## 10. 給執行 Agent 的最終驗收清單（Definition of Done）

- [ ] `node qa.js` → `PASS 1490 / FAIL 0`
- [ ] `node build.js` → 產出 `dist/site/`（10 檔）且 `dist/site/index.html` 存在
- [ ] Cloudflare Pages 專案已建立、名稱為指定值、與其他既有專案隔離
- [ ] `https://<project>.pages.dev` 可開，四分頁與單場頁渲染正常、無 undefined/NaN
- [ ] （若用 token）用畢撤銷該 token
