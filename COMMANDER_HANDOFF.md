# 指揮官交接書

> 我這邊已經完成的所有「不需要你決策、不需要 API key」的工作全部 commit + push 完。
> 這份文件只列**你接下來要做的事**，按時間順序排好。

---

## ✅ 我已完成（不需要你動手）

- DevSwarm 4-agent LangGraph 蜂群骨架（PM/Architect/Coder/QA + self-heal）
- Phase 1 餐飲後端：25 表 SQLAlchemy + 3 套 Alembic 遷移
- 4 個 FastAPI router（orders/stock/clock/events）+ 對應 schemas + services + tests
- 11 個 HTTP endpoint 全部接好、`make api` 後 /docs 可見
- 10 份 DevSwarm 任務簡報（specs/）— 等你填 API key 後就可一鍵跑
- 10 份戰略文件（docs/00-09 + MORNING_BRIEF + 本文）
- 11 commits、106/106 測通過、ruff 全綠、pyright 0 錯誤
- 真實 Postgres 16 + pgvector 0.6.0 已啟動並驗證
- Seed 資料腳本 + End-to-end demo flow 跑通了一個完整 POS 日
- 9 個食安/勞檢/個資/災難 SOP 寫入 `docs/08_safety_compliance.md`
- LINE 三軸統一通道（StubLineMessenger + HttpLineMessenger 骨架）
- DB-level append-only 防護（stock_movements / audit_log / customer_points_ledger 三表 UPDATE/DELETE 全擋）
- 預算煞車（`--budget USD N` 防 DevSwarm 燒錢）
- promote pipeline（`make promote TASK=<id>` 把蜂群產出搬進正式 services/）

---

## 🔴 你**現在**要做的（5 分鐘內）

### 1. 拍 D1-D4 四個決策

| 決策 | 影響 | 你的選擇 |
|---|---|---|
| **D1** 開店日 | 倒推所有里程碑 | 填日期：__________ |
| **D2** POS 選型 | iCHEF / POS+ / 自建 | __________ |
| **D3** 員工載具 | iPad / 手機 / Web | __________ |
| **D4** 硬體採購人 | 標籤機 / 發票機 / 收銀錢箱 誰買 | __________ |

D2 拖過 W2 末 → 軌道 B/C 動不了。**最重要的決策**。

### 2. 填 ANTHROPIC_API_KEY

```bash
cp .env.example .env
# 編輯 .env，把 ANTHROPIC_API_KEY=sk-ant-... 填進去
```

從 https://console.anthropic.com/settings/keys 拿。

---

## 🟡 你**今天/明天**要做的

### 3. 跑驗證

```bash
make test         # 應 106 passed
make typecheck    # 應 0 errors
make full-check   # 跑全部 quality gate
```

### 4. 跑第一個真實 DevSwarm 任務

```bash
make demo         # 真實損益模組（profit_calc）
```

預期：5-10 分鐘、USD $0.5-2、產出 `workspace/<task_id>/real_profit_calculator.py` + 測試。
詳細故障排除見 `docs/07_devswarm_runbook.md`。

### 5. 玩一下 demo data + flow

```bash
make db-up        # 起 Postgres + Redis（你環境若無 docker，已有 native postgres）
make db-migrate   # 套用 3 份 Alembic 遷移
make seed         # 灌一家測試餐廳（王老闆漢堡店、12 道菜、3 顧客）
make api          # 起 FastAPI， http://localhost:8000/docs 看 11 個 endpoint
make demo-flow    # 跑完整 POS 一日鏈：打卡→開單→結帳→報廢→員工餐→下班→彙總
```

---

## 🟢 你**這週**要做的

### 6. 跑剩下 5 個 DevSwarm spec

```bash
make swarm REQ="$(cat specs/bom_consumer.md)"
make swarm REQ="$(cat specs/discount_resolver.md)"
make swarm REQ="$(cat specs/cogs_variance_detector.md)"
make swarm REQ="$(cat specs/labor_hours_classifier.md)"
make swarm REQ="$(cat specs/uniform_invoice_validator.md)"
```

每個應 USD $0.5-2，全部 5 個 < USD $15。
跑完用 `make promote TASK=<id>` 把產出搬進 `restaurant_api/services/`。

### 7. 跟 POS 廠商談（D2 決策的延伸）

iCHEF / POS+ 業務聯絡 → 看 API 文件 → 評估整合工時。
schema 已預留 `external_pos_id` + `pos_source` 欄位，整合層加在 `restaurant_api/integrations/pos/`。

---

## 🟢 你**這個月**要做的

### 8. 找一家試點客戶（不必是你自己的店）

理想條件：
- 月營業額 30-200 萬
- 已用過至少一套 POS（會痛、知道想要什麼）
- 老闆親自參與導入
- 接受 3 個月免費試用 + 共同優化

T2（對外可賣 SaaS）的啟動條件之一。

### 9. 申請 LINE 官方帳號 + 電子發票字軌

- LINE OA：30-60 天審核期，越早越好
- 電子發票字軌：到財政部電子發票整合服務平台申請。每兩月一期，跨期作廢成本高
- 兩者都是 Phase 2 整合會用到的關鍵 ID，**不申請就動不了**

### 10. 開店日 T-30 天起：跑食安 / 勞檢 / 個資 SOP

`docs/08_safety_compliance.md` 是完整 checklist。重點：
- §1 食安事件回溯流程（已有 SQL query）
- §3 個資告知文案（要在 POS 點餐畫面顯示）
- §5 開店/換班/收店 SOP（每天必做）
- §6 災難情境（POS 當機紙本流程要演練一次）

---

## 📊 風險紅燈（每週 review）

| 項目 | 紅燈條件 | 應對 |
|---|---|---|
| **DevSwarm 月成本** | > USD $50 | 查 `make backlog` 與 cache hit 率 |
| **POS 廠商談判** | W2 末未拍板 | 直接自建（FastAPI router 已備好） |
| **電子發票** | 開店前 3 週未申請 | 紙本過渡，違反食安法 |
| **試點客戶** | T1 結束時還沒找到 | T2 啟動條件不滿足，停 T2 規劃 |
| **食安事件** | 開店後任何 1 次 | docs/08 §1.5 SOP 啟動，24h 內通報 |

---

## 📁 你回 repo 後一定要看的 7 個檔案

1. **`README.md`** — 入口
2. **本文 (`COMMANDER_HANDOFF.md`)** — 你的 to-do
3. **`docs/06_execution_plan.md`** — 完整 12 任務 + 4 決策路徑圖
4. **`docs/07_devswarm_runbook.md`** — 跑 `make demo` 的故障排除
5. **`docs/08_safety_compliance.md`** — 食安/勞檢/個資/災難 SOP
6. **`docs/09_phase1_extension_kit.md`** — KDS / 訂位 / LINE 設計決策
7. **`MORNING_BRIEF.md`** — 早晨速覽（這份是 v1 已稍舊，主要看本文）

---

## 🚨 「絕對不要」清單

- 不要把 `.env`（裡面有 API key）push 進 git — gitignore 已擋，但別 force
- 不要把 DevSwarm 指向 production 憑證 — 沙盒不是真容器
- 不要把家人 / 員工 / 試運轉資料當「正式營運資料」帶到開店日 — 重建一個 production DB
- 不要刪除 Google Maps 負評 — 處理但不刪
- 不要在 main 分支直接 push — 用 PR 流程（即使是自己 review）
- 不要為了讓 DevSwarm 跑過就調寬沙盒超時或預算 — spec 寫不好不是預算問題

---

## 一句話

**現在到開店日之間，瓶頸是你的 4 個決策（D1-D4），不是程式碼。**
寫完決策 → 填 API key → `make demo` → 跑光 spec → 6 週後我們就有一個真實能用的單店 POS 系統。

如果需要我繼續處理特定子任務，回我一句話（例如「跑光剩 5 個 spec」、「接 iCHEF 整合層」、「寫 nightly job 框架」），我接著開幹。
