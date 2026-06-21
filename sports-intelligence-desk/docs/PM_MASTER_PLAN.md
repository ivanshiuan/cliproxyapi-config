# Sports Intelligence Desk — Master Plan（賽事投研系統總規劃）

> 角色定位：**頂級量化賽事分析師 + PM**。
> 本文件是把「賽事預測系統」從現在的 v1 推到「機構級投研終端」的完整藍圖，
> 可直接拆給工程端做資料庫、API、模型、前端。
>
> 核心信念（永遠不變）：**不是預測誰會贏，是量化「真實機率 vs 市場價格」的落差(Alpha)。**
> 每場只回答五問：① 真實機率？② 市場定價？③ 哪裡錯價？④ 風險可控？⑤ 該不該納入組合？

---

## 0. 現況盤點（v1 已完成 ✅）

| 模組 | 狀態 | 位置 |
|---|---|---|
| 多因子引擎（8 子模型，Poisson+Dixon-Coles） | ✅ 已測試 | `js/engine.js` |
| 資料層（8 資料庫結構 + 4 場查證資料） | ✅ | `js/data.js` |
| 投研終端 UI（主控台/單場/組合/時間軸，手機 PWA） | ✅ | `index.html` `js/app.js` |
| match-intel Skill（封裝分析流程） | ✅ | `.claude/skills/match-intel/` |
| 賽後回測欄位 | ⚠️ 介面有、邏輯待接 | 時間軸頁 |

**v1 限制（誠實）**：資料為手動查證快照、非即時；歷史交手/球員歷史/裁判庫尚未建；
模型為單點估計、無 Bayesian 區間、無 Monte Carlo、xG 未做球員校正。本計畫即補齊這些。

---

## 1. 系統總架構

```
┌─ 資料層 (11 DB) ──────────────────────────────────────────┐
│ 賽事 · 球隊 · 球員 · 教練 · 盤口 · 環境 · 裁判             │
│ 歷史交手 · 大賽戰績 · 預測紀錄 · 回測 · 四場組合           │
└──────────────┬────────────────────────────────────────────┘
               │ ETL / 清洗 / 特徵工程
┌──────────────▼─ 模型層 ───────────────────────────────────┐
│ 核心 M1–M8  +  進階 A1 Bayesian · A2 Monte Carlo          │
│            A3 球員校正 xG · A4 戰術對位量化 · A5 Ensemble  │
└──────────────┬────────────────────────────────────────────┘
               │ 機率 + 信心區間 + Edge
┌──────────────▼─ 決策層 ───────────────────────────────────┐
│ 單隊 Rating · 單場評級 S/A/B/C/X · 四場組合 Portfolio     │
└──────────────┬────────────────────────────────────────────┘
               │
┌──────────────▼─ 輸出層 ───────────────────────────────────┐
│ 投研 Memo · 投研終端 UI · 警報 · 賽後回測校準             │
└───────────────────────────────────────────────────────────┘
               ▲ 賽前時間軸 T-72h→T-90m 動態重算 ┘
```

---

## 2. 資料層藍圖（11 張表）

> 原則：金額/機率高精度；時間 UTC 存、台灣顯示；每筆有 `source` 與 `as_of`（誠實標來源與時點）。

### 2.1 已建（v1，需擴充深度）
1. **Match** — match_id, comp, group, stage, home/away, neutral, stadium, city, kickoff_utc, env*, referee
2. **Team** — fifa_rank, elo, xg_for/against, gf/ga, form10, squad_value, ppda, directness, set_piece_xg, corners*
3. **Player** — name, role, importance, status(ok/doubt/out)　← 待擴充至球員等級細項（見下）
4. **Coach** — formation, pressing, possession, sub_timing, knockout_record, penalty_record…（待建）
5. **Market** — opening/current/closing odds, movement, sharp signal, implied_prob
6. **Environment** — temp, humidity, wet_bulb, altitude, roof, rest_days, travel, tz_diff
7. **Referee** — cards/game, fouls, penalties, home_bias, var_freq, big_match（待建）

### 2.2 待新建（本計畫核心）
8. **HeadToHead 歷史交手** — 每組對戰歷史：歷屆世界盃交手比分、近 20 年國際賽、同洲際對戰、同氣候/同場地紀錄、PK 大戰
9. **TournamentHistory 大賽戰績** — 各隊歷屆世界盃/洲際盃成績、淘汰賽履歷、小組出線率、心理底蘊分
10. **Prediction 預測紀錄** — 每場每次重算的快照（T-72h…T-90m），模型機率 + 信心區間 + 當時盤口
11. **Backtest 回測** — 實際結果、Brier、Log Loss、校準誤差、錯因標籤
12. **Portfolio 四場組合** — 組成、相關性矩陣、組合分、實際命中

### 2.3 球員等級細項（Player 深度擴充）
- **基本**：國籍/俱樂部/聯賽/位置/年齡/慣用腳
- **近況**：近5場分鐘/進球/助攻/xG/xA
- **體能**：近14天分鐘、連續先發、旅行距離（疲勞模型輸入）
- **傷病**：狀態/復出時間/復出後分鐘
- **進攻**：射門/射正/關鍵傳球/禁區觸球
- **防守**：攔截/搶斷/解圍/空戰成功率
- **門將**：撲救率/PSxG/出擊/PK 撲救
- **定位球**：角球/自由球/點球主罰
- **大賽**：世界盃/歐冠/國家隊淘汰賽經驗

---

## 3. 歷史資料策略（這是普通預測器最弱、我們的護城河）

| 維度 | 用途 | 取得方式 |
|---|---|---|
| 歷屆世界盃交手比分 | 心理/風格對位先驗 | Wikipedia/FIFA 結構化爬取 → HeadToHead 表 |
| 近 20 年國際賽結果 | Elo 校準 + 長期穩定性 | eloratings.net、football-data |
| 球員事件級資料 | 球員校正 xG（A3）、傳球網路、壓迫 | **StatsBomb Open Data**（competitions/matches/events/lineups/360）|
| 即時傷病/先發/停賽 | M6 可用性、T-90m 重算 | Sportmonks / API-Football（lineup, sidelined）|
| 即時/收盤盤口 | M8 Edge、sharp money 偵測 | API-Football pre-match odds / 盤口聚合 |
| 同氣候/同場地紀錄 | 水土適應（M7 增強）| 歷史比賽 + 場館氣候資料合併 |

> **誠實前提**：StatsBomb Open 免費但覆蓋有限；Sportmonks/API-Football 需 API key + 網路。
> v1→v2 先用「開放資料 + 手動查證」起步，付費源在 Phase 3 接入（見路線圖成本段）。

---

## 4. 模型層藍圖

### 4.1 核心 M1–M8（v1 已有，待強化）
| | 模型 | v1 狀態 | 強化方向 |
|---|---|---|---|
| M1 | Team Power（Elo+近況+經驗+教練）| ✅ | 接真實 Elo 動態、對手強度加權 |
| M2 | Expected Goals（攻防乘子⊕Elo supremacy→λ）| ✅ | 加 A3 球員校正 |
| M3 | Score Matrix（Poisson+Dixon-Coles）| ✅ | 升級 A2 Monte Carlo 模擬 |
| M4 | Corners | ✅ | 接真實 corner xG/傳中數據 |
| M5 | Tactical Matchup | ✅ 文字 | 升級 A4 量化分 |
| M6 | Player Availability | ✅ | 接即時 lineup + 疲勞模型 |
| M7 | Environment Stress | ✅ | 真實濕球/海拔/旅行資料 |
| M8 | Market Inefficiency（Edge）| ✅ | 加 sharp money 偵測、盤口時序 |

### 4.2 進階模型（v2 新建，機構級）
- **A1 Bayesian 修正**：小樣本（世界盃只有幾場）用先驗 + 後驗收斂，輸出**機率區間**而非單點。解決「3 場資料就斷言」的過擬合。
- **A2 Monte Carlo**：對 (λH, λA) 抽樣模擬 **10,000 次比分**，產生穩健的 1X2/大小分/波膽分布 + 信賴區間。取代純解析 Poisson。
- **A3 球員校正 xG**：不同球員處理同機會能力不同（巨星把握率高）。用 StatsBomb 事件資料校正 finishing，修正「普通 xG 低估強隊」。
- **A4 戰術對位量化**：把 M5 文字對位（高壓vs弱出球、控球vs低位、快反vs高防線…）轉成數值修正項。
- **A5 Ensemble**：M2-Poisson / A2-MonteCarlo / Elo-1X2 / 市場隱含 四模型加權，權重由**回測 Brier 動態學習**。單一模型會死，集成才穩。

### 4.3 每個進階模型的驗收標準（AC）
- A1：輸出 90% 信賴區間，賽後覆蓋率 ≈ 90%（校準正確）
- A2：10k 模擬 1X2 與解析解差 < 1%，波膽分布平滑
- A3：球員校正後，強隊 xG 平均上修且賽後 Brier 下降
- A5：集成 Brier < 任一單模型 Brier（否則集成無效）

---

## 5. 決策層

- **單隊 Rating** = 基本面 + 球員健康 + 戰術適配 + 近況 + 教練 + 環境 + 大賽經驗（加權）
- **單場評級** S/A/B/C/X（已實作）：S 三方一致核心｜A 優勢明確｜B 變數多低權重｜C 觀望｜X 不碰
- **四場組合 Portfolio**（已實作）：平均信心 − 相關性 − 過熱 − 資訊不完整懲罰
  - v2 加：**相關性矩陣**（同方向/同日/同組/同風格量化）、組合最佳化（選低相關高 Edge 的子集）

---

## 6. 賽前時間軸自動化（動態重算）

| 時點 | 動作 | 自動化目標 |
|---|---|---|
| T-72h | 建基本面、抓賽程/場地/天氣、標高風險 | cron 拉資料 → 初版 Prediction 快照 |
| T-48h | 更新傷病、媒體訓練、盤口 → 重算 | diff 警報（盤口異動/傷病變化）|
| T-24h | 記者會、教練暗示、可能先發、環境 | 戰術對位更新 |
| T-6h | 天氣定稿、盤口大異動、社群消息 | sharp money 偵測 |
| **T-90m** | **抓正式先發 → 重算可用性/xG/比分矩陣** | 自動發「結論升/降級」通知 |
| 賽後 | 記錄預測 vs 實際 → Brier/Log Loss → 校準權重 | 寫入 Backtest，週度重訓 A5 權重 |

---

## 7. 路線圖（Phase 1–4）

### Phase 1 — 資料底盤（7–10 天）
| 任務 | 交付 | AC |
|---|---|---|
| HeadToHead 表 + 爬蟲 | 歷屆世界盃交手 DB | 每組對戰有 ≥1 筆歷史 |
| TournamentHistory 表 | 各隊大賽戰績/淘汰賽履歷 | 32+ 隊齊全 |
| Player 深度擴充 | 球員等級細項 schema + 核心球員填充 | 每隊 ≥5 名核心有近況 |
| Coach + Referee 表 | 教練傾向/裁判尺度 DB | 本屆裁判齊全 |
| 真實 Elo 接入 | eloratings 同步 | 每隊 Elo as_of 標記 |

### Phase 2 — 模型升級（10–21 天）
| 任務 | 交付 | AC | 狀態 |
|---|---|---|---|
| A2 Monte Carlo | 10k 模擬引擎 + 信賴區間 | 與解析差 <1% | ✅ 已完成（mean-preserving）|
| A1 Bayesian | 機率區間輸出 | 覆蓋率 ≈90% | ⏳（MC 的 CV 已是雛形）|
| A3 球員校正 xG | StatsBomb 接入 + finishing 校正 | 賽後 Brier ↓ | ⏳（需資料源，網路受限）|
| A4 戰術量化 | 對位分數化 | 進入 Rating | ✅ 已完成（tacticalScore，量化分入 memo/UI）|
| A5 Ensemble | 加權集成 + 回測學權重 | Brier < 單模型 | ✅ 已完成（w=0.4，回測證實優於模型與市場）|
| 機率校準 | temperature scaling | 可靠度對齊 | ✅ 已完成（γ=1.25）|

### 額外已完成（本輪）
| 任務 | 交付 | 狀態 |
|---|---|---|
| 回測校準引擎 | `js/backtest.js`：Brier/Log Loss/可靠度/基準對比 | ✅ |
| 歷史交手 + 大賽戰績庫 | `js/history.js`：H2H + pedigree（8 隊查證）| ✅ |
| 時間軸頁回測接通 | 有結果自動算分、可靠度表 | ✅ |

### Phase 3 — 投研自動化 + 即時源（21–30 天）
| 任務 | 交付 | AC |
|---|---|---|
| API-Football/Sportmonks 接入 | 即時賽程/傷病/先發/盤口 | `fetchFixtures()` live |
| 賽前時間軸 cron | T-72h→T-90m 自動重算 | 自動快照 + 警報 |
| sharp money 偵測 | 盤口時序異動 | 反向移動自動標記 |
| 自動 Memo 生成 | 10 段投研報告自動產 | 每場一鍵生成 |

### Phase 4 — 高階優化（30–60 天）
| 任務 | 交付 |
|---|---|
| 相關性最佳化組合 | 自動選低相關高 Edge 子集 |
| 盤口異常偵測器 | Market anomaly detector |
| 自學習權重 | 週度回測 → 自動重訓 ensemble |
| 校準儀表板 | Brier/Log Loss 趨勢、可靠度圖 |

---

## 8. MVP 12 件事 — 進度追蹤

| 優先 | 功能 | 狀態 |
|---|---|---|
| P0 | 自動抓賽程 | ⚠️ 內建快照（live 接口已留）|
| P0 | 自動轉台灣時間 | ✅ |
| P0 | 球隊基本面表 | ✅ |
| P0 | 近 10 場戰績 | ✅ |
| P0 | 勝平負模型 | ✅ |
| P0 | 大小分模型 | ✅ |
| P0 | 正確比分矩陣 | ✅ |
| P0 | 四場組合評分 | ✅ |
| P1 | 球員傷病 | ✅ 結構＋查證填充 |
| P1 | 角球模型 | ✅ |
| P1 | 盤口異動 | ✅ |
| P1 | 賽後回測 | ⚠️ 介面有、邏輯 Phase 2 |

---

## 9. 校準與回測（護城河核心）

每場賽後必做：
1. 記錄 **預測機率 vs 實際結果**
2. 算 **Brier Score**（越低越準）+ **Log Loss**
3. 畫 **可靠度圖**（reliability diagram）：說 70% 的事是否真的發生 70%
4. 標 **錯因**（漏傷病/盤口/天氣/輪換/模型偏差）
5. **週度**用累積 Backtest 重訓 A5 集成權重

> 普通人只想要答案。我們的價值在：**知道哪場有優勢、哪場是陷阱、哪場資訊不足不該出手**，且能用回測證明系統真的有 Alpha。

---

## 10. 資料源與成本（誠實）

| 源 | 內容 | 成本 | 階段 |
|---|---|---|---|
| FIFA 官方 / Wikipedia | 賽程/結果/歷史 | 免費 | Phase 1 |
| eloratings.net | Elo | 免費 | Phase 1 |
| StatsBomb Open Data | 事件級/lineup/360 | 免費（覆蓋有限）| Phase 2 |
| API-Football | 即時賽程/傷病/先發/盤口 | 付費（有免費額度）| Phase 3 |
| Sportmonks | lineup/formation/sidelined/裁判 | 付費 | Phase 3 |

---

## 11. 下一步立即行動（建議順序）

1. **建 HeadToHead + TournamentHistory 表**（歷史是護城河，先補）
2. **A2 Monte Carlo 上線**（最快提升輸出穩健度，純算力、不需新資料）
3. **賽後回測邏輯接通**（開始累積 Brier，才談得上校準）
4. **接一個免費即時源**（API-Football 免費額度）讓 `fetchFixtures()` 真 live
5. 之後再上 A1/A3/A5 進階模型

> 原則：**先讓系統能自我證明（回測）+ 輸出穩健（Monte Carlo），再堆資料與進階模型。**
> 不為了 demo 灌確定性；賽前資訊本就不完整，誠實標 verified vs estimated 是紀律。
