# 06 — 完成路徑執行計畫

> 從現狀（Phase 0 完成、Phase 1 骨架）走到「全產業 AI 智慧營運系統」的具體排程。
> 本文與 `03_roadmap.md` 互補：roadmap 講「為什麼分階段」、本文講「具體下一步做什麼」。

---

## 三層完成定義

| 層級 | 完成 = | 估時 | 獨立可賣？ |
|---|---|---|---|
| **T1 — 自家開店可用** | ERP + 真實損益 + BOM + 招待精算 + 打卡 跑在自家店上、零紙本記帳 | 2-3 個月 | ✅ 自用 |
| **T2 — 對外可賣 SaaS** | T1 + 認證 + 多租戶 + CRM + 行銷 + 地圖 + 電子發票 | +3-6 個月 | ✅ 商業化 |
| **T3 — 連鎖總部級** | T2 + 階層權限 + 任務系統 + 加盟稽核 + 三大閉環 | +6-12 個月 | ✅ 加盟 |

每層皆獨立可賣產品。建議**先衝 T1**；T2/T3 等 T1 真實跑 6-8 週後再規劃。

---

## T1 任務拆解（12 項 + 4 個指揮官決策）

### 軌道 A — 計算核心（DevSwarm 全自動產出，純函式 + pytest）

| # | Spec | 模組名 | 狀態 |
|---|---|---|---|
| 1 | `specs/profit_calc.md` | `real_profit_calculator.py` | ✅ 已寫 |
| 2 | `specs/bom_consumer.md` | `bom_consumer.py` | ✅ 已寫 |
| 3 | `specs/discount_resolver.md` | `discount_resolver.py` | ✅ 已寫 |
| 4 | `specs/cogs_variance_detector.md` | `cogs_variance_detector.py` | ✅ 已寫 |
| 5 | `specs/labor_hours_classifier.md` | `labor_hours_classifier.py` | ✅ 已寫 |
| - | `specs/uniform_invoice_validator.md` | `uniform_invoice_validator.py` | ✅ 已寫（bonus） |

執行方式：`make demo` 或 `make swarm REQ=specs/<file>.md`。每個任務預期 USD $0.5-2，全部 6 個 < $15。

### 軌道 B — 業務 Router（規格已就緒，等待手動接到 FastAPI）

| # | Spec | Router 路徑 | 狀態 |
|---|---|---|---|
| 6 | `specs/orders_router.md` | `restaurant_api/routers/orders.py` | ✅ 規格已寫 |
| 7 | `specs/stock_intake_router.md` | `restaurant_api/routers/stock.py` | ✅ 規格已寫 |
| 8 | `specs/clock_router.md` | `restaurant_api/routers/clock.py` | ✅ 規格已寫 |
| 9 | `specs/cost_events_router.md` | `restaurant_api/routers/events.py` | ✅ 規格已寫 |

> Router 不適合用 DevSwarm 跑（Coder 限制是單檔輸出；router 需要 schemas + handlers + DI）。
> 由我手動依規格實作，或未來 DevSwarm 擴充多檔模式後再讓蜂群跑。

### 軌道 C — 必須手動（非 DevSwarm 範圍）

| # | 目標 | 狀態 |
|---|---|---|
| 10 | Alembic 初始遷移（18 表） | ✅ **已產生並驗證** |
| 11 | POS 整合腳本（iCHEF / POS+ / 自建擇一） | 🔴 **等 D2 決策** |
| 12 | 報表 dashboard（簡易 HTML） | 🟡 T1 後半再說 |

---

## 指揮官的 4 個決策（DevSwarm 不能替你決定）

| 決策 | 何時定 | 影響 |
|---|---|---|
| **D1 — 開店日** | 越早越好 | 倒推所有里程碑 |
| **D2 — POS 選型** | W2 末 | 軌道 B/C 都動不了直到拍板 |
| **D3 — 員工介面載具** | W3 | iPad / 手機 / Web 影響介面設計 |
| **D4 — 硬體採購人** | W3 | 標籤機、發票機、收銀錢箱誰買誰裝 |

---

## 8-10 週節奏

| 週 | 指揮官 | AI（接令即跑） |
|---|---|---|
| **W1** | 拍板 D1-D4 / 填 `.env` 的 ANTHROPIC_API_KEY / `make demo` 驗證 | 6 個軌道 A spec 全跑完，產出物進 `workspace/`、再整理進 `restaurant_api/services/` |
| **W2** | review A 軌道產出、跟 POS 廠商談 | 4 個軌道 B router 接到 FastAPI、補測試 |
| **W3-4** | POS 整合驗證、員工/家人試運轉 | 報表 dashboard 起手、報廢/員工餐實戰流程調校 |
| **W5-6** | 真實小規模試運轉（家人或員工） | 監控、告警、bug fix 高速循環 |
| **W7-8** | 開店前壓測 staging → prod | 災難演練、備援 |
| **W9-10** | **開店！** 你是第一個真實用戶 | 即時 hotfix、第一週每日復盤 |

---

## T1 風險紅線

1. **POS 整合卡住** — D2 拖太久軌道 B/C 都動不了，**W2 末必須拍板**
2. **電子發票遲到** — 找實際開店日前 4 週開始申請，文件流程比想像久
3. **DevSwarm 成本失控** — 預算上限 USD $50/月（T1 全跑完應 < $30）；若超過要查 cache hit 率
4. **試運轉資料當底版** — **千萬別把家人試運轉資料當正式數據帶到開店**。資料庫從新建一個
5. **沙盒安全** — DevSwarm 沙盒不是真正安全邊界（v1 限制）；別把 prod 憑證放進開發環境

---

## T2 / T3 觸發條件

不在 T1 期間規劃，避免空轉。觸發條件：

- **T2 啟動** 當：第一家試點客戶找上門、或 T1 跑 8 週後 ROI 已可量化（毛利、流失客挽回、人力節省都有數字）
- **T3 啟動** 當：第一個加盟主完成「克隆我家系統」流程、產生需要稽核總部視角的需求

兩者啟動前各重做一次拆解，不沿用本文。

---

## 「全線執行」當前進度（2026-05-26）

| 項目 | 狀態 |
|---|---|
| Phase 0 — DevSwarm 蜂群骨架 | ✅ 完成、47/47 測綠、Mocked 端到端通過 |
| Phase 1 — 餐飲後端骨架（FastAPI + 18 表 ORM） | ✅ 完成 |
| 戰略文件 6 份（含本文） | ✅ 完成 |
| DevSwarm 任務簡報 6 份（4 calc + 4 router + 1 bonus + 1 demo = 10 spec） | ✅ 完成 |
| Alembic 初始遷移（18 表 470 行） | ✅ **已產生並在 Postgres 16 驗證** |
| CI Workflow | ✅ GitHub Actions 配好 |
| `make` 工具集 | ✅ install / test / lint / fmt / demo / swarm / db-up / api / status |
| Lint | ✅ ruff 全綠 |
| Git push | ✅ branch `claude/autonomous-resttech-enterprise-oW9jp` |

剩下未做的全部都是「等指揮官決策」或「等 API key 才能執行的雲端動作」：

- 跑 `make demo` 驗證 DevSwarm 端到端（需 ANTHROPIC_API_KEY）
- POS 整合（等 D2）
- 報表 dashboard（T1 後半）
- 雲端部署（W7-8）

---

## 給指揮官的一句話結論

**現在所有「不需要你的決策、不需要 API key」的東西全部就緒。**
**你的下一步只剩兩件事：(1) 拍 D1-D4，(2) 在 `.env` 填 API key 然後 `make demo`。**
其他都可以在你決定後 24 小時內接到位。
