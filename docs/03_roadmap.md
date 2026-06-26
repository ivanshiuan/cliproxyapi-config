# 03 — Roadmap（路線圖｜Phase 0 → Phase 5）

**Status:** Executive roadmap. Concrete dates, concrete exit criteria, concrete cost.
**Anchor date:** 2026-05-26（指揮官開店倒數中）.
**Scope:** All phases, with hard exit criteria, cost estimates, risks, success metrics.
**SSOT alignment:** [`./00_vision.md`](./00_vision.md) §七 開發階段路徑.
**Companion:** [`./01_tech_stack_recommendation.md`](./01_tech_stack_recommendation.md), [`./02_devswarm_architecture.md`](./02_devswarm_architecture.md), [`./04_data_schema.md`](./04_data_schema.md).

> 本文不寫 vague aspiration。每個 phase 的 exit criteria 都可以被一條 `pytest` 或一份 KPI dashboard 證明已達成。

> ⚠️ **時序/階段/資本口徑的 SSOT 是 [`./20_master_plan.md`](./20_master_plan.md)（總綱）**。本文(03)聚焦各 Phase 的 exit criteria 與成本;
> 與總綱衝突處以總綱為準。兩點重大收斂(細節見 docs/20)：
> 1. **本文假設「店已存在」,缺 pre-launch 軸**。開店前 T-90 籌備(選址/財務/證照/人力)見 [`./19_restaurant_launch_blueprint.md`](./19_restaurant_launch_blueprint.md);**Gate 0 財務驗證未過,不啟動 Phase 1 的開店。**
> 2. **養成遊戲化(員工留任)階段對位**：最小子集屬 **P1 開店 Day-1**、完整後端(5 spec)屬 **P1.5(T0+1~2 月)**,見 [`./18_employee_pet_gamification.md`](./18_employee_pet_gamification.md);本文 §6.1.3 的「人資/現場調度**自動閉環**」仍屬 P5。
> 3. **成本表(§7)只含研發 ~470 萬,未含開店本體 CapEx+週轉 ~250 萬**;真實總資本需求 ~720 萬,見 docs/20 §6。

---

## 0. 大局時間軸

```
2026-05  2026-08      2026-11        2027-02        2027-05        2027-11
   │       │             │              │              │              │
   ▼       ▼             ▼              ▼              ▼              ▼
 [P0] ─► [P1] ───────► [P2] ─────────► [P3] ─────────► [P4] ─────────► [P5]
DevSwarm  單店 MVP      CRM + 行銷      地圖 + 區域     連鎖 / 加盟    全 AI 自主
(this    (commander    (multi-tenant   (PostGIS,      (K8s, multi-   (預測、優化、
 repo)    opens store)  RLS, LINE)     Google Maps)    store HQ)      三大閉環)
```

| Phase | 期程 | 啟動 | 完成 | 累計人月 | 累計現金成本 (TWD) |
|---|---|---|---|---:|---:|
| P0 DevSwarm | 1 月 | 2026-05 | 2026-06 | 1 | ~10 萬 |
| P1 單店 MVP | 2–3 月 | 2026-06 | 2026-08 | 4 | ~80 萬 |
| P2 CRM + 行銷 | 3 月 | 2026-08 | 2026-11 | 7 | ~160 萬 |
| P3 地圖 / 區域 | 3 月 | 2026-11 | 2027-02 | 10 | ~240 萬 |
| P4 連鎖 / 加盟 | 3 月 | 2027-02 | 2027-05 | 13 | ~330 萬 |
| P5 全 AI 自主 | 6 月 | 2027-05 | 2027-11 | 19 | ~460 萬 |

> 「人月」與「現金成本」皆為估算；DevSwarm 持續成熟後人月可再壓低。

---

## 1. Phase 0 — DevSwarm Skeleton（蜂群骨架）

**期間：** 2026-05-26 → 2026-06-25（4 週）
**屬性：** 本 repo 唯一交付物。RestSwarm 尚未存在。

### 1.1 範圍

- LangGraph 4-Agent 蜂群（PM / Architect / Coder / QA）
- 自我修復循環（max_heal_iters=5）
- 單任務 CLI：`python -m devswarm --task-file specs/<task>.md`
- 沙盒：`workspace/<task_id>/` + rlimits + pytest subprocess
- Anthropic prompt caching 全面啟用
- 成本 / 使用量追蹤
- 結構化 audit log

### 1.2 Demo target

撰寫 spec：`specs/profit_calc.md` —— 一個會計算「真實淨利」的 Python 模組，需正確處理招待、折扣、報廢、員工餐五類分流，含 pytest 測試。

這個 demo 同時是 Phase 1 真實損益模組的 dry-run，未來可直接搬進 RestSwarm。

### 1.3 Hard exit criteria（每條都可被自動驗證）

| # | 條件 | 驗證方式 |
|---|---|---|
| 1 | `python -m devswarm --task-file specs/profit_calc.md` 跑完 exit code 0 | CI smoke test |
| 2 | 產出檔案於 `workspace/profit_calc/` 含 `src/profit.py` + `tests/test_profit.py` | filesystem check |
| 3 | `pytest workspace/profit_calc/` 全綠 | exit code 0 |
| 4 | 至少有一個 heal iteration 在歷史 audit log 中（證明 loop 真的運作） | grep audit log |
| 5 | Token 使用報告中 `cache_read_input_tokens > 0`（證明 caching 生效） | parse final_state.json |
| 6 | 單任務總成本 < USD 5（demo task 規模） | usage report |
| 7 | README + docs/00–04 全部寫完 | doc review |
| 8 | `.env.example` 完整、`.gitignore` 阻擋 secret 與 workspace | repo audit |

### 1.4 非範圍（Phase 0 不做）

- 任何餐飲業務邏輯（profit_calc 是抽象 demo，不是 RestSwarm 正式模組）
- Web UI、PR 自動化、DevOps Agent
- 多任務平行、checkpoint resume
- 非 Python code-gen

### 1.5 成本估算

| 項目 | 估算 |
|---|---|
| 指揮官時間 | 4 週（規格 + 驗收） |
| Anthropic API（開發+測試） | ~USD 100 |
| 雲端 | 0（本機開發） |
| **Total** | ~10 萬 TWD（主要為機會成本） |

---

## 2. Phase 1 — 極窄 MVP（單店）

**期間：** 2026-06-25 → 2026-08-25（2 個月）
**屬性：** **指揮官自家店開幕日商用目標**。MVP 的「窄」是刻意——只做開店第一天必須有、缺了會出事的東西。

### 2.1 模組清單

| 模組 | 內容 | 對應 schema 區段 |
|---|---|---|
| ERP 進銷存 | 食材進貨、庫存、報廢、盤點 | `stock_movements`, `inventory_snapshots`, `suppliers` |
| BOM 配方扣料 | 每筆訂單即時扣料、異常 AI 警示 | `bom`, `menu_items`, `order_lines` |
| 真實損益報表 | 含固定成本攤提、隱藏成本拆解 | `daily_pl`, `cost_buckets` |
| 五類分流計帳 | 招待 / 折扣 / 折讓 / 報廢 / 員工餐 + 試吃 | `transaction_categories`, `adjustments` |
| 基礎人事 | 打卡 / 請假 / 換班、工時統計 | `employees`, `attendance`, `leave_requests`, `shifts` |
| 最小 admin UI | Next.js 14 dashboard（只給老闆看） | — |

> 完整 schema 由另一 agent 在 [`./04_data_schema.md`](./04_data_schema.md) 維護。本文不複寫 DDL。

### 2.2 技術選型

完全依 [`./01_tech_stack_recommendation.md`](./01_tech_stack_recommendation.md) §5：
- Backend: FastAPI + SQLAlchemy 2 async + Postgres 16
- 部署: 單台 VM + Docker Compose + Caddy
- 認證: HTTP Basic + IP allowlist（單店暫用，Phase 2 換 OIDC）

### 2.3 DevSwarm 在 Phase 1 的角色

蜂群會被餵入每個模組的 spec（一份 markdown）：

```
specs/
├── erp_inventory.md
├── bom_deduction.md
├── real_pl_report.md
├── adjustment_categories.md
├── attendance.md
└── admin_ui_min.md
```

每份 spec 經過 PM → Architect → Coder → QA loop，產出 PR。**每個模組獨立驗收**，不互相 block。

### 2.4 Hard exit criteria（獨立可測）

| # | 條件 | 驗證 |
|---|---|---|
| 1 | 每日真實損益自動跑出（含固定成本攤提、隱藏成本拆解） | end-of-day cron + 對照人工試算 |
| 2 | 每筆訂單即時 BOM 扣料 | order create → `stock_movements` 自動 insert |
| 3 | 報廢 / 員工餐 / 試吃 / 招待 / 折扣 五類分流計帳，互不污染 | 損益報表分項可一鍵展開 |
| 4 | 打卡 / 請假 / 換班完整紀錄 | 人事報表月結通過 |
| 5 | 開店日（D-day）可從 POS（或暫代輸入介面）連續 14 天無資料遺失 | uptime + DB integrity check |
| 6 | 真實損益日報跟指揮官手算誤差 < 0.5% | 兩週並行對賬 |
| 7 | 所有金額 `numeric(14,4)`、所有時間戳 UTC、所有 PK UUIDv7 | schema lint |
| 8 | DevSwarm 平均每模組 heal iters ≤ 3 | audit log 統計 |

### 2.5 風險

| 風險 | 緩解 |
|---|---|
| 開店日壓力 → 容易讓步、放水驗收 | exit criteria 寫死，不可動 |
| BOM 標準化現場做不下去 | 開店前兩週進行模擬出單，校準配方 |
| 真實損益模組計算錯誤 | 雙重對賬期 14 天，期間人工帳本平行跑 |
| 第一版 admin UI 過於陽春 | 接受；只給指揮官一人用，Phase 2 才優化 |

### 2.6 成本估算

| 項目 | 估算 (TWD) |
|---|---:|
| 指揮官時間 | 8 週 |
| Anthropic API（蜂群跑模組） | ~5 萬 |
| 雲端 VM（Hetzner / GCP） | ~1 萬 |
| 網域、SSL、Cloudflare、監控 | ~1 萬 |
| 外部金流測試帳 | ~0.5 萬 |
| **Phase 1 新增** | **~80 萬**（主要為機會成本 + 雜支） |

---

## 3. Phase 2 — CRM + 行銷自動化

**期間：** 2026-08-25 → 2026-11-25（3 個月）

### 3.1 範圍

- 會員體系（手機 / LINE 綁定）
- 顧客消費畫像、AI 自動分群
- 自動推播（生日、回購提醒、喚回）
- LINE Messaging API 整合
- 電子發票串接（大平台 API）
- 多租戶 RLS 正式啟用（為日後加盟做準備）
- 認證系統升級：OIDC（Keycloak 或 Auth.js）+ 多角色權限矩陣

### 3.2 模組

| 模組 | 內容 |
|---|---|
| 會員資料 | tier、tag、lifetime value、RFM |
| 推播 / 活動 | LINE 訊息、優惠券、活動 ROI 計算 |
| 電子發票 | 開立、作廢、捐贈、載具歸戶、中獎對獎 |
| 多租戶 | RLS policy、租戶後台、租戶 onboarding flow |
| 行銷成效 | 曝光 → 引流 → 轉單 → 回購 funnel |

### 3.3 Hard exit criteria

| # | 條件 |
|---|---|
| 1 | LINE 推播從 admin UI 一鍵發送，到達率 ≥ 95% |
| 2 | 每筆銷售自動開立電子發票，當日上傳成功率 ≥ 99% |
| 3 | RLS policy 在 fuzzing test 下，跨租戶資料洩漏 = 0 |
| 4 | 行銷活動 ROI 報表（帶客數、轉單率、毛利貢獻）可一鍵產出 |
| 5 | RFM 分群結果可在後台視覺化、且支援匯出（CSV/Excel） |
| 6 | 認證 + 角色權限通過 OWASP ASVS Level 2 自查 |

### 3.4 風險

| 風險 | 緩解 |
|---|---|
| LINE 官方帳號驗證 / 升級流程卡關 | 提早 30 天送審 |
| 電子發票大平台 SLA 不穩 | 設 fallback queue + 重試策略 |
| RLS 啟用時 query plan 退化 | 上線前壓測 + index 補齊 |

### 3.5 成本

| 項目 | 估算 (TWD) |
|---|---:|
| Anthropic API（蜂群） | ~8 萬 |
| 雲端 + 觀測 | ~3 萬 |
| LINE 官方帳號月費 | ~1 萬 |
| 電子發票 API（大平台月費 + 量計費） | ~3 萬 |
| Sentry / Grafana Cloud | ~2 萬 |
| **Phase 2 新增** | **~80 萬** |
| **累計** | **~160 萬** |

---

## 4. Phase 3 — Google 地圖 + 區域數據

**期間：** 2026-11-25 → 2027-02-25（3 個月）

### 4.1 範圍

- Google Business Profile API 整合：營業時間、菜單、照片同步
- Google Maps Platform：定位、距離矩陣、Geocoding
- PostGIS 啟用：商圈半徑分析、競品熱區
- 區域消費力 / 人流數據導入（政府 open data + 第三方）
- 地圖資訊錯誤 AI 偵測（門市資料 vs Google 實際資料對照）
- 新店選址數據參考引擎（pandas 分析 + pgvector 相似店比對）

### 4.2 Hard exit criteria

| # | 條件 |
|---|---|
| 1 | 任一門市的 Google Business Profile 可從 admin UI 一鍵同步 |
| 2 | 區域競品（半徑 1km）報表 5 秒內回應 |
| 3 | 選址評分引擎輸出與 commander 經驗判斷 Pearson r > 0.7 |
| 4 | Google API quota 用量監控告警 < 80% 容量 |

### 4.3 風險

| 風險 | 緩解 |
|---|---|
| Google API 計費昂貴 | 設用量上限、結果重度快取（Redis 24h TTL） |
| 政府 open data 更新頻率低 | 接受；產出標註資料新鮮度 |
| GBP API 限制（單帳戶店數） | 多帳戶輪詢策略 |

### 4.4 成本

| 項目 | 估算 (TWD) |
|---|---:|
| Google Maps Platform | ~5 萬 |
| Anthropic API | ~10 萬 |
| 雲端（PostGIS + 分析） | ~5 萬 |
| **Phase 3 新增** | **~80 萬** |
| **累計** | **~240 萬** |

---

## 5. Phase 4 — 總部 / 連鎖 / 加盟

**期間：** 2027-02-25 → 2027-05-25（3 個月）

### 5.1 範圍

- 階層權限：老闆 / 店長 / 主廚 / 幹部 / 總部
- 任務系統：發布、追蹤、KPI 監控、SLA
- 多店總部總覽 dashboard
- 加盟店稽核（自動化異常偵測）
- SOP 系統化（版本控、簽收紀錄）
- 異常 AI 預警引擎
- **K8s 上線**：GKE / EKS + Helm chart（由 DevOps Agent 產出）
- Redis Streams 事件匯流

### 5.2 Hard exit criteria

| # | 條件 |
|---|---|
| 1 | 多店總覽即時延遲 < 5 秒 |
| 2 | 加盟稽核自動跑日、異常案件自動派工 |
| 3 | 權限矩陣支援動態調整（無需重啟） |
| 4 | K8s 部署 SLA ≥ 99.9%（一個月內計算） |
| 5 | 任務系統包含 SLA 倒數 / 自動升級規則 |
| 6 | 加盟店上線 onboarding ≤ 1 個工作天 |

### 5.3 風險

| 風險 | 緩解 |
|---|---|
| K8s 維運 know-how 不足 | 用 managed（GKE / EKS）、不要自建 |
| 加盟店硬體 / 網路品質不一 | 設離線快取、最終一致性 |
| 多店資料量爆增 | 早期 schema 已支援分區；Phase 4 啟用 partition by tenant_id |

### 5.4 成本

| 項目 | 估算 (TWD) |
|---|---:|
| K8s（GKE / EKS） | ~10 萬 |
| 連鎖節點觀測 / 監控 | ~5 萬 |
| Anthropic API | ~15 萬 |
| 法遵 / 加盟合約模板 | ~5 萬 |
| **Phase 4 新增** | **~90 萬** |
| **累計** | **~330 萬** |

---

## 6. Phase 5 — 完整 AI 自主運行

**期間：** 2027-05-25 → 2027-11-25（6 個月）

### 6.1 範圍：三大閉環啟動

依 [`./00_vision.md`](./00_vision.md) §五 Layer 2：

#### 6.1.1 營收 / 自媒體永動閉環
數據分析 → 行銷策略 → 創意生成（影音 / 圖）→ 社群矩陣自動發佈 → LINE CRM 精準推播

| 子模組 | 內容 |
|---|---|
| 內容創意 Agent | Anthropic + 影像 API；產出貼文 / 短影音腳本 |
| 社群矩陣 | FB / IG / Threads / YouTube Shorts 自動排程 |
| LINE 精準推播 | 結合 RFM + 即時行為訊號 |

#### 6.1.2 供應鏈 / 財務無人閉環
點餐扣料 → 天氣 / 歷史預測 → 自動詢價 → 採購單一鍵審批 → 電子錢包對帳付款

| 子模組 | 內容 |
|---|---|
| 需求預測 | LightGBM / sklearn / Prophet pipeline |
| 自動詢價 | 供應商 API + LINE 群組爬抓（合法授權）|
| 對賬 | 銀行 API + 電子錢包對接 |

#### 6.1.3 人資 / 現場調度閉環
出餐超時偵測 → 排班比對 → 個人化 SOP 微課程推送 → 自動配對資深員工

| 子模組 | 內容 |
|---|---|
| 出餐計時 | POS 事件流 + 預期時間比對 |
| 排班優化器 | 線性規劃 / OR-Tools |
| 微課程推送 | LINE / 內部 App，含驗收測驗 |

### 6.2 預測 / 優化建議引擎（Decision Layer）

- 每日 AI 主動產出 5–10 條 actionable 建議（不是儀表板，是「行動清單」）
- 建議的後驗追蹤：採用 / 結果 / ROI 自動回饋
- 自我演化：好建議的 pattern 進入 prompt library

### 6.3 Hard exit criteria

| # | 條件 |
|---|---|
| 1 | 三大閉環 24/7 自動跑滿 30 天，無人工介入單日 ≥ 25 天 |
| 2 | 預測準度（需求量）MAPE < 12% |
| 3 | AI 建議採用率 ≥ 30%（指揮官 / 店長視角） |
| 4 | 人均管理工時下降 ≥ 40%（vs Phase 4 同期） |
| 5 | 內容自動發佈引流 → 帶單 ROI 可量化、且為正 |

### 6.4 風險

| 風險 | 緩解 |
|---|---|
| LLM 自主決策失誤造成財務 / 法務損失 | 所有金錢動作保留「需人類核可」門檻；超過 X 元自動 hold |
| 預測模型 cold-start 不準 | 啟用前 3 個月不接生產動作，純 shadow run |
| 平台 ToS（FB / LINE / IG）變動 | 多通路冗餘、不依賴單一平台 |
| AI 創意品質下降影響品牌 | 強制人類抽審樣本（10%）+ 品牌風格守則注入 prompt |

### 6.5 成本

| 項目 | 估算 (TWD) |
|---|---:|
| Anthropic + 影像 / 影音生成 API | ~40 萬 |
| 預測模型訓練（雲端 GPU 偶用） | ~10 萬 |
| 通路費用（內容矩陣 / 工具訂閱） | ~15 萬 |
| 法律 / 合規顧問 | ~10 萬 |
| 連續觀測 / SRE | ~15 萬 |
| 雜支與緩衝 | ~40 萬 |
| **Phase 5 新增** | **~130 萬** |
| **累計** | **~460 萬** |

---

## 7. Cumulative timeline & cost table（彙整）

| Phase | 結束日 | 期程 | 新增現金 (TWD) | 累計現金 (TWD) | 對照願景 §八 真人版 |
|---|---|---|---:|---:|---:|
| P0 DevSwarm | 2026-06-25 | 4 週 | 10 萬 | 10 萬 | — |
| P1 單店 MVP | 2026-08-25 | 2 月 | 80 萬 | 90 萬 | — |
| P2 CRM | 2026-11-25 | 3 月 | 80 萬 | 170 萬 | — |
| P3 地圖 | 2027-02-25 | 3 月 | 80 萬 | 250 萬 | — |
| P4 連鎖 | 2027-05-25 | 3 月 | 90 萬 | 340 萬 | 340 萬（6 個月真人） |
| P5 全 AI | 2027-11-25 | 6 月 | 130 萬 | **470 萬** | 推估 700+ 萬 |

> 願景 §八 估真人版「6 個月 340 萬」只涵蓋我們現在的 P0-P4 範圍但更窄；DevSwarm 版多吃了 P5 全自主，總成本仍壓在外包報價（800–1200 萬）一半以下。

---

## 8. Risk register（Top 5 風險與緩解）

| # | 風險 | Likelihood | Impact | 緩解 |
|---|---|---|---|---|
| 1 | **LLM 成本失控**（蜂群跑爆 token） | High | Medium | §1.3 #5 caching 強制；CLI `--max-cost-usd`；逐 phase 編列預算 ceiling；每週成本回顧 |
| 2 | **沙盒逃逸 / 蜂群亂寫檔** | Medium | High | [`./02_devswarm_architecture.md`](./02_devswarm_architecture.md) §7 + §12；強制 VM 隔離；不持 prod credential |
| 3 | **Schema drift（蜂群產出與 04 schema 不一致）** | High | High | 每模組 spec 必須附 schema 章節引用；CI 跑 Alembic head + Pydantic schema vs DB introspection |
| 4 | **第三方 API 變動**（LINE / ECPay / Google / 電子發票大平台） | Medium | Medium | 抽 vendor adapter；每月跑 contract test；建立 vendor changelog watch |
| 5 | **人才 / AI 成熟度落差**（蜂群停滯不前） | Medium | High | 每 phase 結尾保留 1 週 swarm 自我升級時間；版本不滿意可延長 phase；P5 前可啟動有限度真人介入 |

次級風險：

- 法遵變動（個資法、PCI-DSS、電子發票）→ §3、§6 已預列法律顧問費
- 開店日意外（指揮官個人健康 / 商業條件）→ P1 exit criteria 鎖死技術交付；商業上線可彈性
- Anthropic / Claude 服務變動 → §11 換 provider 抽象層保留

---

## 9. Success metrics per phase（成功指標）

每階段同時跑 **operational metric**（系統健康）與 **business metric**（商業結果）。

### 9.1 Operational metrics

| Phase | Metric | 目標 |
|---|---|---:|
| P0 | DevSwarm 任務通過率（包含 ≤5 heal） | ≥ 95% |
| P0 | 單任務平均 token 成本 | < USD 3 |
| P1 | 系統 uptime | ≥ 99.5% |
| P1 | 訂單 → BOM 扣料延遲 | < 2 秒 |
| P2 | LINE 推播到達率 | ≥ 95% |
| P2 | 電子發票上傳成功率 | ≥ 99% |
| P3 | 競品報表 P95 latency | < 5 秒 |
| P4 | K8s SLA | ≥ 99.9% |
| P4 | 加盟店 onboarding 時間 | ≤ 1 工作天 |
| P5 | 閉環無人介入比例 | ≥ 80% |
| P5 | 預測 MAPE（需求量） | < 12% |

### 9.2 Business metrics

| Phase | Metric | 目標 |
|---|---|---|
| P1 | 指揮官店真實淨利準確度 vs 手算 | 誤差 < 0.5% |
| P1 | 隱藏成本（招待 / 折扣 / 報廢）月結對賬準確度 | 100% |
| P2 | 會員回購率 vs Phase 1 | +20% |
| P2 | 行銷活動正 ROI 比例 | ≥ 70% |
| P3 | 區域競品分析覆蓋率（自店周邊 1km） | ≥ 90% |
| P3 | 選址引擎商業採信度 | 指揮官 yes/no 對齊 ≥ 70% |
| P4 | 加盟稽核發現的異常處置時效 | < 24 小時 |
| P4 | 多店毛利率變異監控告警準確率 | ≥ 80% |
| P5 | AI 建議採用率 | ≥ 30% |
| P5 | 人均管理工時下降 | ≥ 40% |
| P5 | 流失客喚回貢獻營收 | 每月持續報表化 |

---

## 10. Governance（治理）

### 10.1 Phase gate review

每階段結束前 1 週，指揮官召開 phase gate review：

1. Exit criteria 逐條對表（pass / fail / waived）
2. Operational + business metric review
3. Risk register 更新
4. 下一階段 spec 完成度 / 預算簽核

未通過 ≥ 80% 必要條件 → phase 延展，不推進。

### 10.2 文件變更

- `00_vision.md` 變更須 PR + 指揮官 approve（最高層級）
- `01–04` 變更須 ADR + 對應交付物未動工方可改
- 本文（03）變更須注明 phase 重排理由與成本影響

### 10.3 預算守門

| 預算上限 | 觸發動作 |
|---|---|
| Phase 預算 ≥ 80% | 黃燈：暫停新模組接案、檢討 token 用量 |
| Phase 預算 ≥ 95% | 紅燈：phase 凍結，必須由指揮官手動解凍 |
| 單任務 cost ≥ USD 50 | swarm CLI 自動中止並要求人類確認 |

---

## 11. 與其他文件的關係

| 文件 | 關係 |
|---|---|
| [`./00_vision.md`](./00_vision.md) | 上游 SSOT；本文是 §七 的時間 / 成本 / 守門條件化版 |
| [`./01_tech_stack_recommendation.md`](./01_tech_stack_recommendation.md) | 平行；本文每階段引入的 stack 必須 trace 回 §5 |
| [`./02_devswarm_architecture.md`](./02_devswarm_architecture.md) | 平行；本文 Phase 0 = 它的全部 |
| [`./04_data_schema.md`](./04_data_schema.md) | 平行；本文 Phase 1 模組必須 trace 回 schema 章節 |
