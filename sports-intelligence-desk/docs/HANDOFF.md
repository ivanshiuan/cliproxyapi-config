# SPORTS INTELLIGENCE DESK — 交接文件（HANDOFF）

> 最後更新：2026-06-21 ｜ 分支：`claude/sports-intelligence-desk-dayved`

---

## 📌 2026-06-21 深度復盤 + UIUX 升級（PR #2）

**做了什麼**
- UIUX 全版位升級（`terminal.css` v2：玻璃質感/漸層/發光等級/進場動畫，類別名相容、邏輯零改）
- PWA 圖示 `icon.svg`（manifest any+maskable、index、sw v4、build 納入）
- 回應 CodeRabbit **三輪審查項目全數修復並 resolve**（安全/XSS/健全性/lint/文件一致性；逐條對應見各 fix commit）
- **深度復盤**再抓到 `esc()` 套用不一致（戰術/歷史/設定頁），已補齊

**驗證**：`node qa.js 100` → **14900/14900**；backtest 可複現；clean-clone build 通過；CSS 括號平衡。

**誠實揭露的殘留風險（非 bug）**
1. **UI 像素級/RWD 實機**：此環境無瀏覽器，QA 只證明 HTML 渲染乾淨，無法保證版面 → 需開單檔或部署後實機驗收。
2. **模型真實 Alpha**：Edge 資訊量<50%、純模型未贏市場（僅 A5 集成贏）、評級衡量價差非準度 → 須賽後回填 `SID.results` 累積。
3. **Live 面**：只更新賽程/比分/傷病狀態，其餘 curated。
4. **CI `Test (Python 3.12)` 紅燈**：既有、與本 JS PR 無關（`restaurant_api` 缺 `apscheduler`，pyright 失敗）。

**待辦（使用者動作）**：① Cloudflare 部署 `498-win.pages.dev` ② merge PR #2。
> 一句話：把每場球賽當「即將開盤的資產」，輸出「真實機率 vs 市場價格的落差(Alpha)」的純前端投研系統。

---

## 1. 現在是什麼狀態（誠實版）

| 項目 | 狀態 |
|---|---|
| 機械正確性 | ✅ `node qa.js` 1490/1490；30 輪壓測 4470/4470 全綠 |
| 回測 | ✅ `node backtest_sim.js` 5000 場可複現（種子固定） |
| 引擎 8 子模型 | ✅ M1-M8 + A2 蒙地卡羅 + A4 戰術量化 + A5 集成 |
| Live 對接層 | ✅ 已寫好（3 家 API + 傷病同步 + 自動降級） |
| 部署 | ✅ Cloudflare Worker proxy + wrangler.toml + 設定頁 |
| 實戰 Alpha 驗證 | ⏳ 需賽後回填 `SID.results` 累積（尚無真實賽果） |

**可上線**（純靜態，拖到 Pages/Netlify/GitHub Pages 即可）。

---

## 2. 這次 session 做了什麼（2026-06-20）

徹底復盤後修了 **QA 抓不到** 的問題（不是機械層）：

1. **`engine.js`**：移除死碼 `agree` 與孤兒函式 `gradeKey`（算了從未使用，原意像評級門檻漏接）。
2. **`live.js`**：thesportsdb provider `cfg.date` 未設 → `d=undefined`，加今天日期退路。
3. **`live.js` 新增傷病同步**（補設計缺口）：API-Football `/injuries` 端點，
   **只比對 curated 名單已存在球員、依姓氏比對、抓不到靜默降級**，回傳 `injuriesApplied`。
   守住「未查證傷病不復活」鐵律 —— 絕不無中生有新球員。
4. **QA 加 4 斷言**覆蓋傷病同步（mock fetch，驗證只動 curated、不造假、用後還原狀態）。
5. 同步文件：`LIVE_DATA.md`、設定頁說明、`data.js` 註解。

---

## 3. ⚠️ 必須誠實揭露的限制（不是 bug，是邊界）

讀回測輸出就會看到，**機率機械層全綠 ≠ 真有 Alpha**：

- **Edge 偵測資訊量 48.4%（< 50%）**：在合成 DGP 回測裡，模型聲稱的 1X2 edge
  「比市場更接近真相」的比例低於擲硬幣。→ **別把畫面上的 Edge 數字當真實獲利保證。**
- **模型未穩定優於市場**（Brier 差 −0.0016）；**集成(模型 40% + 市場 60%)才小幅勝出**。
  → A5 集成是目前最該信的輸出，不是純模型。
- **分級 vs 準確度非單調**：回測中 A 級 Brier(0.61) 比 B(0.54)/C(0.50) 還高。
  原因：高 edge 標的天生落在更難預測（接近 5:5/爆冷）的區間，**分級衡量的是「價差」不是「準度」**。
  這是設計取捨，但使用者容易誤解 —— 已在此載明。
- **真實 Alpha 仍待實戰**：賽後把比分填進 `SID.results`，`backtest.js` 才會累積真實命中率。

---

## 4. 關於「刷最新先發/傷病」（任務 A）

**這個沙盒環境抓不到 live**：egress 白名單實測 api-sports.io / football-data 回 403。
所以即使有 key，我在這裡也無法 fetch。對接「程式」已就緒，差「通路」：

- **路 B（建議）**：到 Claude Code on the web 環境網路設定，把
  `v3.football.api-sports.io`、`api.football-data.org`、`www.thesportsdb.com`
  加進 egress 允許清單 → 我就能在此自動抓。
- **路 C（產品化）**：部署 `deploy/worker.js`（Cloudflare Worker），
  `wrangler secret put APIFOOTBALL_KEY` 注入 key（**key 不要貼在對話**，會進 log），
  設定頁 proxy 填 `https://<worker>.workers.dev/?u=`，按「測試連線並更新」。

> 在通路打通前，**傷病維持 2026-06-19 已查證快照，不填任何未驗證資料**（鐵律）。

---

## 5. 檔案地圖

```
sports-intelligence-desk/
├── index.html / css/ / js/        # 純前端 SPA（無建置步驟）
│   ├── data.js      # 8 資料庫：teams / players / matches / odds / results
│   ├── engine.js    # M1-M8 + MC + 戰術量化 + 集成（核心智慧）
│   ├── history.js   # 歷史交手 / 大賽戰績
│   ├── backtest.js  # Brier / LogLoss / 可靠度 / 基準比較
│   ├── live.js      # 3 家 API 對接 + 傷病同步 + 降級
│   └── app.js       # 路由 / 渲染 / Memo / 設定頁
├── qa.js            # 上線前 10 輪復盤（node qa.js [N]）
├── backtest_sim.js  # 5000 場合成回測（校準/Edge 資訊量驗證）
├── deploy/          # Cloudflare Worker proxy + wrangler.toml
└── docs/            # PM_MASTER_PLAN / BACKTEST_REPORT / LIVE_DATA / 本檔
```

---

## 6. 下一步（建議優先序）

1. **打通 live 通路**（路 B 或 C）→ 自動同步賽程/比分/傷病。
2. **賽後回填 `SID.results`** → 啟動真實 Alpha 累積（這才是系統價值的最終證明）。
3. （可選）把其他 provider 也補上傷病端點；目前只有 API-Football 有。
4. （可選）分級語意調整：明確標示「S/A = 價差大」不等於「準度高」，降低誤解。

---

## 7. 快速指令

```bash
node qa.js          # 10 輪復盤（必過才上線）
node qa.js 30       # 30 輪壓測
node backtest_sim.js  # 5000 場回測報告
```
