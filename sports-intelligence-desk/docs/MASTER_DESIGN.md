# SPORTS INTELLIGENCE DESK — 主設計文件（MASTER DESIGN）

> **版本**：v1.0（2026-06-21）｜**狀態**：P0 全實作、功能正確性 100%、真實 Alpha 待實戰驗證
> **用途**：這份是**單一、完整、可交付**的系統設計文件。可整份丟給另一位 PM / 另一套 AI
> 來審查與優化——文件已包含「現狀精確規格」＋「結構化優化議題（含難度/預期收益）」。
> **原始碼真相來源**：`sports-intelligence-desk/`（本文件所有數字均對齊實作，非願景灌水）。

---

## 0. 給接手 PM 的閱讀指南

| 你想做的事 | 直接看 |
|---|---|
| 30 秒搞懂這是什麼 | §1 定位 + §2 核心理念 |
| 評估技術可行性 | §4 架構 + §5 資料模型 + §6 引擎 |
| 找可優化的點 | **§12 優化議題 backlog**（最有價值）+ §11 誠實限制 |
| 驗證宣稱的準度 | §7 校準與回測證據（含「已證實 / 未證實」分界）|
| 規劃下一階段 | §13 v1→v2 路線圖 |

**一句話交接**：這是一套**零後端、純靜態、可離線**的足球賽事「投研終端」。它不預測比分，
而是把每場球當「即將開盤的資產」，用 8 個子模型算出**真實機率**，與**市場盤口隱含機率**相減，
找出**錯價（Alpha）**，並用評級 / 信心分 / 組合風險把「該不該碰」量化。

---

## 1. 產品定位

- **名稱**：Sports Intelligence Desk（SID）／世界盃賽事投研終端
- **形態**：Progressive Web App（單頁、可安裝、離線可用），手機優先
- **使用者**：足球賽事的量化分析者 / 投研者（把下注決策當投資組合管理）
- **不是什麼**：不是「告訴你誰會贏」的預測器；不是博彩平台；不接金流；不給「明牌」。

---

## 2. 核心理念（產品哲學，務必保留）

> **不是預測比賽，是評估「市場價格」與「真實機率」之間的落差（Alpha）。**
> 每場比賽 = 一檔即將開盤的資產。每場只回答五件事：
> ① 真實機率多少？② 市場怎麼定價？③ 哪裡有錯價？④ 風險可控嗎？⑤ 該不該納入組合？

**鐵律（已寫進 `.claude/skills/match-intel/SKILL.md`）**：
所有機率 / λ / Edge / 評級**一律由引擎計算**，嚴禁憑感覺手寫數字。引擎是護城河，
人的價值在**資料品質**與**解讀**。傷病 / 先發未經查證者**一律不填**（寧缺勿假）。

---

## 3. 範圍

### P0 已實作（v1 現狀）
- 8 子模型引擎（M1–M8）+ Monte Carlo（A2）+ 戰術量化（A4）+ 集成（A5）+ 組合評分
- 4 場 2026 世界盃真實對戰內建快照（H/G 組第二輪，賠口已查證）
- 完整 SPA：儀表板 / 單場深析 / 四場組合 / 時間軸回測 / 設定頁
- Live 資料對接層（3 家 API adapter + 傷病同步 + 自動降級）
- 賽後回測 / 校準基建（Brier / LogLoss / 可靠度）
- Cloudflare Worker CORS 代理 + 一鍵部署自動化
- PWA（manifest + service worker 離線快取）

### 非目標（明確排除）
- 金流 / 下注執行 / 帳戶系統
- 即時比分直播逐秒推播（只做賽程 / 比分 / 傷病狀態合併）
- 多運動（目前只football；架構可延伸但未做）
- 伺服器端持久化（刻意零後端，見 §4 設計取捨）

---

## 4. 系統架構

### 4.1 分層

```
┌─────────────────────────────────────────────────────────┐
│  PWA 前端（純靜態，零後端）                                  │
│                                                           │
│  index.html → 依序載入 6 個 JS 模組（掛在 window.SID 命名空間）│
│                                                           │
│   data.js ──── 8 資料庫（球隊/球員/賽程/環境/盤口/結果）       │
│   history.js ─ 歷史交手 + 大賽戰績                          │
│   engine.js ── 8 子模型 + MC + 集成 + 組合（純函式、無 IO）   │
│   backtest.js ─ 賽後 Brier/LogLoss/可靠度                  │
│   live.js ──── 3 家 API adapter + refresh + 傷病同步        │
│   app.js ───── UI 控制器（渲染所有畫面 + memo 生成）          │
│                                                           │
│   sw.js ────── service worker（cache-first 離線）          │
│   css/terminal.css ─ 設計系統（主題變數/手機優先）            │
└─────────────────────────────────────────────────────────┘
                 │ (選用，僅 Live 模式需要)
                 ▼
┌─────────────────────────────────────────────────────────┐
│  Cloudflare Worker CORS 代理（deploy/worker.js）            │
│  ?u= 目標白名單驗證 → 注入 API key（env secret）→ 轉發 + CORS  │
└─────────────────────────────────────────────────────────┘
                 │
                 ▼   API-Football / football-data / TheSportsDB
```

### 4.2 技術棧

| 項 | 選擇 | 理由 |
|---|---|---|
| 前端 | 原生 ES5/ES6 JS（無框架、無打包器依賴）| 零相依、可離線、單檔可開、壽命長 |
| 樣式 | 純 CSS（CSS 變數設計系統）| 同上 |
| 引擎 | 純函式 JS（`window.SID` 命名空間）| 可測、可移植到 Node（backtest_sim.js 即在 Node 跑）|
| 打包 | `build.js`（把 CSS+6 JS inline 成單檔 HTML）| 產出 `dist/SID-standalone.html` + `dist/site/` |
| 託管 | Cloudflare Pages（靜態）| 免費、全球 CDN、零維運 |
| 代理 | Cloudflare Worker（僅 Live）| 解 CORS + 藏 API key |
| PWA | manifest + service worker | 可安裝、離線 |

### 4.3 關鍵設計取捨

- **零後端**：所有運算在瀏覽器端。優點＝零維運、零成本、可離線、隱私（資料不外送）；
  代價＝無集中式資料累積（賽後結果要靠 `SID.results` 手動 / Live 回填）。
- **命名空間單例**：所有模組掛 `window.SID`，載入順序固定（data → history → engine → …）。
  優點＝簡單；代價＝全域狀態、無模組邊界（見 §12 優化議題 OPT-7）。
- **引擎 / 畫面解耦**：引擎是純函式，換資料源（快照→Live）畫面與引擎完全不用改。

---

## 5. 資料模型（投研欄位總表）

所有數值是**賽前情報快照**。基準：`SID.TOURNAMENT_AVG_XG = 1.30`（世界盃單隊場均 xG）。

### 5.1 TEAM｜球隊（基本面）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `elo` | num | eloratings 近似（每 100 Elo ≈ 0.28 期望淨勝球）|
| `fifa` | num | FIFA 排名 |
| `xg_for` / `xg_against` | num | 賽前 12 個月加權場均 xG（攻 / 防）|
| `form10` | [勝,平,負] | 近 10 場 |
| `wc_exp` / `coach` | 0–100 | 大賽經驗 / 教練臨場量化分 |
| `possession` | % | 控球率 |
| `ppda` | num | 每次防守動作對方傳球數，**越低壓迫越強** |
| `directness` | num | 打法直接性（高＝快反）|
| `set_piece_xg` | num | 定位球 xG |
| `corners_for/against` | num | 場均角球 |
| `squad_value` | 億€ | 陣容身價 |

### 5.2 PLAYER｜球員資產（只列影響模型的核心 / 缺陣）
| 欄位 | 值 | 影響 |
|---|---|---|
| `status` | ok / doubt / out | doubt = 半折（sev 0.5）、out = 全折（sev 1.0）|
| `importance` | 0–1 | 對球隊 xG 的影響權重 |
| `role` | ATT/MID/GK/DEF | ATT/MID→進攻 λ；GK→失球 λ；DEF→被反擊/定位球 |

### 5.3 MATCH + ENV + MARKET
- **MATCH**：`kickoff_utc`（ISO，畫面用 Asia/Taipei 轉）、組別、階段、場館、裁判。
- **ENV**：`temp`/`humidity`/`altitude`/`roof`/`rest_home|away`/`travel_home|away`/`tz_diff`。
- **MARKET（odds）**：莊家十進位賠率（含抽水）`home/draw/away`、`over25/under25`、
  `btts_yes/no`、`opening_home`（開盤價，算盤口移動）、`sharp`（聰明錢方向標記）。

### 5.4 RESULTS｜賽後結果（回測用）
`SID.results["matchId"] = { gh, ga }`（主隊 / 客隊進球）。未填者回測自動略過。

### 5.5 HISTORY｜歷史（history.js）
歷史交手（H2H）+ 大賽戰績 pedigree。`SID.history.lookup(m, home, away)`。

---

## 6. 引擎設計（8 子模型 pipeline — 精確規格）

> 全部對齊 `js/engine.js`。pipeline：M1+M2（含 M6/M7）→ M3 → 衍生市場 → M4/M5/A4 → M8 → 評級 → A2 → A5。

### M1 Team Power（戰力）
Elo 期望勝率 `1/(1+10^((eloB−eloA)/400))`；近況 `form10`、`wc_exp`、`coach` 作微調乘子。

### M2 Expected Goals（期望進球 λ）— 雙訊號融合
1. **攻防乘子 Poisson**：`λ_xg = base × (本隊xg_for/base) × (對手xg_against/base)`
2. **Elo supremacy**：`sup = clamp(eloDiff/100 × 0.28, ±2.6)`；`λ_elo = totalXg/2 ± sup/2`
   （補強 xG 對強隊的低估）
3. **融合**：`λ = (0.5·λ_xg + 0.5·λ_elo) × 近況 × 經驗/教練 × M6 × M7`，clamp 到 `[0.18, 4.2]`

### M6 Availability（球員可用性）→ λ 乘子
核心進攻缺 → 進攻乘子 ↓（`importance × sev × 0.6`）；門將缺 → 失球乘子 ↑（×0.8）；
後防缺 → 被攻 ↑（×0.5）。輸出 `attackMult∈[0.55,1.05]`、`concedeMult∈[0.95,1.6]`。

### M7 Environment（環境體能折損）
**濕球溫度** `wetBulb = temp − (1−humidity/100)·(temp−14)·0.6`；≥27°C 時高壓打法（ppda<10）扣更多。
時差 ≥8h、長途移動 ≥3000km、休息 <4 天各有體能 / 信心扣分。輸出主客各自 λ 乘子 + 信心扣分。

### M3 Score Matrix（比分矩陣）— Poisson + Dixon-Coles
9×9（MAXG=8）矩陣 `P(i,j) = Poisson(i,λH)·Poisson(j,λA)·τ(i,j)`，`ρ = −0.06` 修正低比分/和局低估，正規化。
**所有市場由矩陣導出**：1X2、大小分（1.5/2.5/3.5）、BTTS、正確比分排序。

**校準（關鍵）**：1X2 經 **temperature scaling**，`p' ∝ p^γ`，**γ=1.25**（由 8000 場回測樣本內外擬合，
兩種子一致、非過擬合），修正 Poisson 對熱門勝率的壓縮。校準後供評級 / Edge / 顯示。

### M4 Corners（角球）
基於場均，依 Elo 壓制差 `(eloDiff/300)`、打法直接性、落後追分傾向調整；輸出總角球 + over 9.5 機率。

### M5 / A4 Tactical（戰術對位）
- **M5**：風格對位 → **文字化**突破口（高壓 vs 弱出球、控球 vs 鐵桶、戰力懸殊、定位球）。
- **A4**：把對位**量化**成 `score∈[−1,1]`（正＝利主隊）+ `magnitude`（決定性）+ drivers。
  **純資訊 / 評等用，不改動已校準機率**（避免重新校準）。

### M8 Market（市場隱含機率 + Edge）
去抽水：`隱含 = (1/賠率) / Σ(1/賠率)`。輸出 `overround`、`movement`（開盤−現價，>0＝買盤湧入）、`sharp`。
**Edge = 模型機率 − 市場隱含機率**（五個市場：主/和/客/大2.5/小2.5）。

### 評級 + 信心分
- **三大 Edge** 排序取最大 `maxEdge`；`conviction = max(1X2)`。
- **風險分**：球員缺(+12)、環境(+confPenalty)、盤口反向(+8)、熱門過熱無價差陷阱(+10)、主結論為和局(+6)。
- **信心分**：`conf = clamp(50 + maxEdge·220 + (conviction−0.4)·40 − risk, 5, 97)`。
- **評級**：
  | 級 | 條件 |
  |---|---|
  | **S** | maxEdge≥0.06 且 risk≤12 且 conviction≥0.5 |
  | **A** | maxEdge≥0.035 且 risk≤20 |
  | **B** | maxEdge≥0.02 |
  | **C** | 其餘 |
  | **X**（避開）| risk≥25 或 maxEdge<0，或 risk≥30 |

### A2 Monte Carlo（參數不確定性）
不只從固定 λ 抽（那只重現解析解），而是先對 λ 加 log-normal 不確定性（`cv`，反映賽前資訊不完整），
每次抽一組 (λH,λA) 再抽比分，n=10000 次聚合。**mean-preserving**（drift = cv²/2，不偏離解析期望）。
資料越糊（有傷病 / 環境壓力 / 小樣本黑馬）→ cv 越大 → 區間越寬（誠實放寬）。輸出勝率 90% 信賴區間。

### A5 Ensemble（集成）
洞察：模型誤差與市場誤差**不相關**。`集成 = w·模型 + (1−w)·市場`，**w = 0.4**（`SID.ENS_W`，回測擬合）。
**UI 單場頁以集成為「最準機率」頭條；Edge（純模型 vs 市場）保留為高風險投機訊號。**

### Portfolio（四場組合評分）
`score = clamp(平均信心 − 相關性懲罰 − 過熱 − 資訊不全 − live風險, 0, 100)`。
**相關性懲罰**：四場同日(+8)、含同組(+5)、多數押勝負方向(+7)、大小分方向集中(+6)。
verdict：≥85 主組合 / ≥75 降權 / ≥65 僅參考 / ≥55 不建議 / <55 放棄。

---

## 7. 校準與回測證據（誠實版 — 已證實 / 未證實分界）

跑法：`node backtest_sim.js 8000`（種子固定可複現）。DGP 用 log-linear（與引擎公式**異構、非循環**）。

| 指標（8000 場）| 校準後 (γ=1.25) | 解讀 |
|---|---|---|
| Brier 真相（下限）| 0.5329 | 理論最佳 |
| **集成（模型0.4+市場0.6）** | **0.5393** ✅ | 同時優於模型與市場 |
| 模型 | 0.5464 | 距下限僅 0.013 |
| 市場（含抽水）| 0.5451 | 近神諭 |
| 均勻亂猜 | 0.6667 | 基準 |

**校準後可靠度**（預測 vs 實際）：70–80%→74.6/76.3、80–90%→84.1/88.5、90–100%→91.7/95.7（幾乎對齊）。

### ✅ 已證實
1. **模型有真實資訊量**（Skill 18% vs 均勻，距理論下限僅 0.013）。
2. **校準有效**（熱門低估已修，可靠度對齊）。
3. **集成優於模型與市場**（誤差不相關，加權有效）。
4. **分級有效**（S 級平均 Brier 0.44 << 其他級）。
5. **功能正確性 100%**（QA 300 次 / 42,600 斷言、100 次 / 14,400 斷言皆 0 失敗）。

### ⚠️ 未證實（必須對接手 PM 講清楚）
**真實預測 Alpha 未證實**。回測的模擬市場是用真相機率造的、近乎神諭，本就極難打敗；
真 Alpha 來自真實盤口的結構性低效（散戶情緒 / 熱門偏誤 / 資訊延遲），
**只能靠實戰賽後回測累積證明，無法用模擬代替**。→ 這是 §12 OPT-1 的核心。

---

## 8. UI / UX

| 畫面 | 內容 |
|---|---|
| **儀表板** | 四場總覽 + 警報（S 級機會 / X 級避開 / 高風險）|
| **單場深析** | 集成機率頭條、機率長條、9×9 比分熱力圖、Edge 表、評級 + 信心、MC 區間、戰術突破口、歷史 |
| **四場組合** | Portfolio score + verdict + 相關性懲罰明細 + 排序 |
| **時間軸 / 回測** | 賽後 Brier / 可靠度（填 `SID.results` 後）|
| **設定** | Live provider / proxy 設定 + 手動 refresh |
| **Memo 生成** | 一鍵產出賽前投研 memo（全程 HTML escape 防 XSS）|

設計系統：終端機風格、玻璃質感 / 漸層 / 發光等級、手機優先、可安裝 PWA、離線可用。

---

## 9. Live 資料對接

- **三家 adapter**：API-Football、football-data.org、TheSportsDB，各自把 fixtures normalize 成內部結構。
- **`SID.refresh()`**：合併開賽時間 / 比分到 `SID.matches`/`SID.results`；傷病以**姓氏比對**，
  **只套用到既有 curated 球員**（不憑空新增，守鐵律）；離線時優雅降級回快照。
- **CORS 代理 Worker**：`?u=` 目標白名單驗證 → 注入 host 對應 API key（Worker env secret）→ 轉發 + CORS + 短快取。

---

## 10. 部署架構

- **靜態站** → Cloudflare Pages 專案 `498-win`（`https://498-win.pages.dev`）。
- **一鍵部署**：`make deploy PROJECT=498-win` → `deploy.sh`（驗 token → 預檢 egress → build → `wrangler pages deploy` → 印網址）。
- **前置（一次性）**：環境變數 `CLOUDFLARE_API_TOKEN`（Pages:Edit）；網路 egress 允許 `api.cloudflare.com`、`*.cloudflare.com`。
- **三種部署法**：Dashboard 拖拉上傳（最簡）/ wrangler CLI / Connect-to-Git 自動部署。

---

## 11. 誠實限制 / 已知風險（非 bug，但接手 PM 必看）

1. **UI 像素級 / RWD 實機未驗**：建置環境無瀏覽器，QA 只證 HTML 渲染乾淨，版面需實機驗收。
2. **真實 Alpha 未證**（見 §7）——目前能證的是「有資訊量 + 校準好 + 集成贏模擬市場」，不是「贏真實莊家」。
3. **資料是人工快照**：球隊基本面 / 盤口為手動查證的時點值，會過時；Live 只更新賽程 / 比分 / 傷病狀態。
4. **小樣本資料庫**：目前僅 8 隊、4 場。擴充到整屆需大量資料工程（見 OPT-2）。
5. **無持久化**：賽後結果不自動累積（零後端取捨）；回測樣本靠人工 / Live 回填。
6. **參數多為專家先驗**：M4/M6/M7/A4 的係數（如角球 ×1.5、門將 ×0.8、濕球 27°C 門檻）是人工設定，未資料擬合（見 OPT-3）。

---

## 12. 優化議題 backlog（交接重點 — 給接手 PM/AI 直接挑）

> 每項含：問題、方向、難度（S/M/L）、預期收益、相依。**OPT-1 是唯一能證明商業價值的關鍵路徑。**

| ID | 議題 | 方向 | 難度 | 預期收益 |
|---|---|---|---|---|
| **OPT-1** | **真實 Alpha 未證** | 建立**實戰賽後回測管線**：每輪賽後回填 `SID.results`，持續追蹤模型 / 集成 Brier 是否**長期低於真實莊家收盤價**。這是唯一能把「有資訊量」變成「有商業價值」的證據。 | M | **決定性**（沒這個＝玩具）|
| **OPT-2** | 資料覆蓋只有 8 隊 4 場 | 把 data layer 接到完整資料源（整屆 / 多聯賽），自動化基本面更新管線。 | L | 高（可用性）|
| **OPT-3** | 係數多為專家先驗 | 對 M4/M6/M7/A4 係數做**資料擬合**（用歷史賽果回歸），取代人工拍腦袋。 | L | 中高（準度）|
| **OPT-4** | 集成權重固定 w=0.4 | 改成**動態權重**（依賽事類型 / 資料完整度 / 市場流動性調整），甚至 stacking。 | M | 中 |
| **OPT-5** | Edge 偵測在模擬中表現差 | 重新定義 Edge 訊號：只在**真實低效市場特徵**（盤口反向、聰明錢、資訊延遲）出現時才觸發。 | M | 高（與 OPT-1 綁）|
| **OPT-6** | 傷病 / 先發靠人工查證 | 接 injury API 自動同步 + 先發確認（守「未證不填」鐵律下做自動化）。 | M | 中 |
| **OPT-7** | 全域 `window.SID` 單例、無模組邊界 | 重構成 ES module / 加型別（TS）/ 單元測試覆蓋引擎每個子模型。 | M | 中（可維護性）|
| **OPT-8** | 校準只做 1X2 | 對大小分 / BTTS / 角球也做獨立校準與回測。 | M | 中 |
| **OPT-9** | 無使用者層持久化 | 若要累積跨裝置回測 / 個人組合歷史，需引入輕後端（如 Cloudflare D1/KV）——**會打破零後端取捨，需權衡**。 | L | 視商業模式 |
| **OPT-10** | 多運動延伸 | 引擎抽象化（λ / 矩陣 / 市場）以支援其他運動。 | L | 視策略 |

**建議優先序**：OPT-1 →（OPT-5 + OPT-3）→ OPT-2 → 其餘。
理由：先證明在**真實市場**有 Alpha（OPT-1/5/3），再投資規模化（OPT-2）；
基礎建設（OPT-7）與商業模式相關項（OPT-9）依團隊決策插入。

---

## 13. v1 → v2 路線圖

- **v1（現狀）**：單日四場投研終端、引擎 + 校準 + 集成 + 組合、可離線、可部署。✅
- **v1.1**：實戰回測管線（OPT-1）+ Live 傷病自動同步（OPT-6）+ 部署上線（498-win）。
- **v1.2**：係數資料擬合（OPT-3）+ Edge 訊號重定義（OPT-5）+ 多市場校準（OPT-8）。
- **v2**：完整資料覆蓋（OPT-2）+ 動態集成（OPT-4）+ 引擎模組化 / TS（OPT-7）。
- **v2+（需決策）**：輕後端持久化（OPT-9）/ 多運動（OPT-10）。

---

## 14. 附錄

### 14.1 檔案地圖
```
sports-intelligence-desk/
├── index.html              入口（固定載入順序）
├── css/terminal.css        設計系統
├── js/
│   ├── data.js             8 資料庫
│   ├── history.js          歷史交手 + 大賽戰績
│   ├── engine.js           ★ 8 子模型 + MC + 集成 + 組合（核心）
│   ├── backtest.js         賽後 Brier/LogLoss/可靠度
│   ├── live.js             3 家 adapter + refresh + 傷病同步
│   └── app.js              UI 控制器 + memo
├── sw.js / manifest.webmanifest / icon.svg   PWA
├── build.js                打包（inline 成單檔 + dist/site/）
├── qa.js                   10 輪 / 多次復盤 QA 斷言
├── backtest_sim.js         5000–8000 場合成回測（Node）
├── deploy.sh               一鍵部署（驗 token/egress→build→wrangler）
├── Makefile                build/serve/qa/backtest/deploy
├── deploy/                 Cloudflare Worker（worker.js + wrangler.toml）
└── docs/                   本文件 + SYSTEM_BLUEPRINT / PM_MASTER_PLAN /
                            BUILD_SPEC / BACKTEST_REPORT / LIVE_DATA /
                            DEPLOY_CLOUDFLARE / HANDOFF
```

### 14.2 名詞表
| 詞 | 義 |
|---|---|
| **Alpha** | 真實機率與市場隱含機率的落差（錯價）|
| **xG** | Expected Goals，期望進球 |
| **λ (lambda)** | Poisson 期望進球率 |
| **Elo** | 棋手評分制改用於球隊戰力 |
| **Dixon-Coles** | 對獨立 Poisson 低比分 / 和局低估的修正 |
| **Brier / LogLoss** | 機率預測準度指標（越低越好）|
| **overround** | 莊家抽水（隱含機率總和 − 1）|
| **PPDA** | 每次防守動作對方傳球數（壓迫強度，越低越強）|
| **temperature scaling** | 用 `p^γ` 重新校準機率 |
| **Ensemble** | 模型 + 市場加權集成 |

---

> **交接結語**：這套系統的**工程與機率方法已紮實（功能 100%、校準有效、集成贏模擬市場）**，
> 缺的是**真實市場的 Alpha 證明**與**資料規模化**。給接手者最該攻的是 §12 **OPT-1**——
> 沒有實戰回測證據，再漂亮的引擎都只是「有資訊量的玩具」；有了它，才談得上投研產品。
