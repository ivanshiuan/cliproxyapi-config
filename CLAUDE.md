# CLAUDE.md — 專案記憶

> 這個檔案是 Claude Code 每次開新 session 自動載入的「專案說明書」。
> 不要把臨時筆記寫在這 — 用 `docs/` 或 `MORNING_BRIEF.md`。
> 這份只放**會影響每次決策**的長期資訊。

---

## 一句話這是什麼

**台灣全域 AI 餐飲智慧營運作業系統**。雙層架構：
- **DevSwarm**（LangGraph 4-agent 蜂群：PM → Architect → Coder → QA）→ 自動產出程式碼（~2,600 LOC）
- **RestSwarm**（FastAPI + 26-table PG + LINE）→ 真實餐飲後端（~12,800 LOC）

完整願景：`docs/00_vision.md`、路徑：`docs/06_execution_plan.md`。

---

## 技術棧（不要問、直接用）

| 層 | 技術 | 版本 |
|---|---|---|
| Python | CPython | 3.12 |
| 後端框架 | FastAPI | 0.136 |
| ORM | SQLAlchemy async | 2.x |
| 資料庫 | PostgreSQL | 16 (+ pgvector 0.6) |
| Async driver | asyncpg | 0.30+ |
| Sync driver (Alembic) | psycopg | 3.2+ |
| Migrations | Alembic | 1.18 |
| 排程 | APScheduler | 3.x |
| 驗證 | Pydantic v2 | ≥2.5 |
| HTTP client | httpx | 0.28 |
| LLM SDK | anthropic | 0.104 |
| Agent 編排 | LangGraph | 0.6 |
| Lint | ruff | latest |
| Type check | pyright | basic mode |
| Test | pytest + pytest-asyncio | 8.x / 0.24 |
| Coverage | pytest-cov | latest |
| 包管理 | pip + pyproject.toml | editable install |

虛擬環境永遠在 `.venv/`。所有指令前綴 `.venv/bin/`。

---

## 目錄結構

```
.
├── CLAUDE.md                    # ← 你正在讀
├── COMMANDER_HANDOFF.md         # 指揮官 to-do 清單
├── MORNING_BRIEF.md             # 早晨速覽（v1 偏舊）
├── CHANGELOG.md                 # 版本變更記錄
├── README.md                    # 對外說明
├── Makefile                     # 40+ 個 make 目標，必看 `make help`
├── pyproject.toml               # 依賴 + ruff + pyright + pytest + coverage 全配置
├── requirements.txt             # pinned 依賴（部署用）
├── .pre-commit-config.yaml      # ruff + secrets/env guard + pyright hooks
├── .env.example                 # 環境變數範本
│
├── .claude/                     # Claude Code 專案配置
│   ├── settings.json            # hooks + permissions
│   ├── commands/                # 自訂 slash commands（check/handoff/morning/spec/swarm）
│   └── agents/                  # 正式 subagent（spec-writer/router-implementer/restaurant-domain-expert）
│
├── .github/                     # CI workflows
│
├── devswarm/                    # AI 蜂群本體（~2,600 LOC）
│   ├── cli.py / __main__.py     # 入口
│   ├── graph.py                 # LangGraph 拓撲
│   ├── state.py                 # SwarmState TypedDict
│   ├── config.py                # 模型選擇 + 計價
│   ├── llm.py                   # Anthropic SDK wrapper (prompt cache)
│   ├── sandbox.py               # pytest subprocess
│   ├── workspace.py             # 沙盒檔案系統
│   ├── prompts/                 # 4 agent 系統提示 + `_versions.py` 版本登記
│   └── nodes/                   # 4 agent node 實作（pm/architect/coder/qa + _common）
│
├── restaurant_api/              # Phase 1 餐飲後端（~12,800 LOC）
│   ├── main.py                  # FastAPI app + 冪等 include_router + middleware
│   ├── config.py                # Pydantic Settings
│   ├── database.py              # async engine + session
│   ├── docker-compose.yml       # PG + Redis（開發用）
│   ├── docker-compose.production.yml  # 真實上線 stack
│   ├── Dockerfile               # multi-stage、non-root、tini PID-1
│   ├── initdb/                  # 01_extensions.sql（pgvector 等擴充）
│   ├── alembic/versions/        # 5 份遷移（initial → closed-loop → KDS/訂位 → 國定假日 → order.customer_id）
│   ├── models/                  # 26 表 SQLAlchemy（tenants/stores/menu/orders/inventory/hr/cash/customers/reservations/cost_events/audit/embeddings/public_holidays）
│   ├── middleware/              # RequestContext + 結構化 JSON 日誌
│   ├── api/                     # deps.py + errors.py + health.py（/live + /ready）
│   ├── schemas/                 # Pydantic 請求/回應（每 router 一檔）
│   ├── services/                # 業務邏輯（純 async，無 HTTP）
│   │   └── calc/                # 純函式計算引擎（無 DB / 無 HTTP / Decimal-everywhere）
│   ├── routers/                 # FastAPI APIRouter（clock/customers/events/kitchen/orders/reservations/stock）
│   ├── jobs/                    # APScheduler 背景任務（expiry_warning/points_expire/cogs_variance）
│   └── integrations/line/       # LINE 統一通道（messenger Stub + HTTP skeleton）
│
├── docs/                        # 11 份戰略文件
│   ├── 00_vision.md             # SSOT
│   ├── 01_tech_stack_recommendation.md
│   ├── 02_devswarm_architecture.md
│   ├── 03_roadmap.md            # Phase 0-5
│   ├── 04_data_schema.md        # DDL + mv_daily_pnl 損益視圖
│   ├── 06_execution_plan.md     # 任務 + 決策路徑
│   ├── 07_devswarm_runbook.md   # `make demo` 故障排除
│   ├── 08_safety_compliance.md  # 食安/勞檢/個資/災難 SOP
│   ├── 09_phase1_extension_kit.md  # KDS / 訂位 / LINE 設計
│   ├── 10_claude_code_workflow.md  # 何時用什麼 Claude Code 能力
│   └── 11_production_deployment.md # Docker / Cloudflare / 部署 SOP
│
├── specs/                       # 10 份 DevSwarm 任務簡報
│   ├── profit_calc.md           # 真實損益（demo 用，已實作於 calc/）
│   ├── uniform_invoice_validator.md   # 已實作於 calc/
│   ├── bom_consumer.md          # 已實作於 calc/
│   ├── discount_resolver.md     # 已實作於 calc/
│   ├── cogs_variance_detector.md # 已實作於 calc/
│   ├── labor_hours_classifier.md # 已實作於 calc/
│   ├── orders_router.md         # 已實作
│   ├── stock_intake_router.md   # 已實作
│   ├── clock_router.md          # 已實作
│   └── cost_events_router.md    # 已實作
│
├── scripts/                     # 操作腳本
│   ├── backlog.py               # `make backlog` 列出所有 spec 狀態
│   ├── promote.py               # `make promote TASK=<id>` 搬蜂群產出
│   ├── seed_demo_data.py        # `make seed` 灌測試餐廳
│   ├── seed_tw_holidays.py      # 灌台灣國定假日
│   ├── demo_flow.py             # `make demo-flow` 跑完整 POS 一日
│   ├── export_openapi.py        # `make openapi` 匯出 OpenAPI schema
│   ├── check_migration_safety.py # `make migration-safety` 掃危險遷移
│   └── smoke_db.py              # `make db-smoke` DB 端到端驗證
│
├── tests/                       # 292 個 pytest
│   ├── conftest.py              # DB savepoint fixtures + AsyncClient
│   ├── routers/                 # router 整合測（real PG）
│   ├── services/                # calc 純函式單元測（profit/bom/discount/cogs/labor/invoice/holiday）
│   ├── jobs/                    # 背景任務測（points_expire 等）
│   ├── test_state.py / test_workspace.py / test_sandbox.py
│   ├── test_graph_mock.py       # mocked end-to-end DevSwarm
│   ├── test_audit_service.py / test_middleware.py / test_health_endpoints.py
│   ├── test_line_integration.py / test_openapi_export.py / test_jobs.py
│   └── test_restaurant_api.py / test_imports.py
│
└── workspace/                   # DevSwarm 產出（gitignored）
```

---

## 系統現況（已落地的功能切片）

| 模組 | Router | Service | 狀態 |
|---|---|---|---|
| 點餐 / 結帳 | `orders.py` | `orders_service.py` | ✅ 含 customer_id 閉環 |
| 進貨 / 庫存 | `stock.py` | `stock_service.py` | ✅ |
| 打卡 / 工時 | `clock.py` | `clock_service.py` | ✅ |
| 成本事件（報廢/員工餐/試菜） | `events.py` | `events_service.py` | ✅ |
| KDS 廚房顯示 | `kitchen.py` | `kitchen_service.py` | ✅ 狀態機轉換 |
| 訂位 + 候位 | `reservations.py`（含 `queue_router`） | `reservation_service.py` | ✅ 狀態機 guard |
| 會員 / 點數 | `customers.py` | `customers_service.py` | ✅ CRUD + 賺點/兌點/到期閉環 |
| 國定假日 | — | `holiday_calendar.py` | ✅ 真實台灣假日查詢 |
| 稽核 | — | `audit_service.py` | ✅ append-only audit_log |
| 純計算引擎 | — | `calc/`（6 模組） | ✅ DevSwarm 產出已 promote |

背景任務（`make jobs` / `make jobs-once`）：`expiry_warning`（食材到期預警）、`points_expire`（點數到期）、`cogs_variance`（COGS 異常偵測）。

---

## 不變法則（每次都遵守）

### 程式碼
- 所有金錢用 `Decimal`，永遠不要 `float`
- 所有 ORM money 欄位用 `Money = Numeric(14, 4)` 別名（在 `models/base.py`）
- 所有 timestamp tz-aware：DB 存 UTC、API 回 `Asia/Taipei`
- 所有 ORM 主鍵 UUIDv7（用 `models/base.uuid7()`）
- 所有業務表必須有 `tenant_id`、`created_at`、`updated_at`
- 顧客面記錄用 `deleted_at` 軟刪除
- Ledger 表（stock_movements、audit_log、customer_points_ledger）**append-only**（DB-level RULE 已擋 UPDATE/DELETE）
- 純計算邏輯放 `services/calc/`：**無 DB、無 HTTP、Decimal-everywhere、純函式**（DevSwarm 產出的標準形狀）
- Pydantic 輸入 `model_config = ConfigDict(frozen=True)` + 用 `BeforeValidator` 拒絕 float（不要用 `strict=True` 會擋 JSON UUID 字串）
- Domain 例外用 `restaurant_api/api/errors.py` 的 `DomainError` 系列，不要用 raw `HTTPException`
- 寫稽核紀錄一律走 `services/audit_service.audit()`，不要直接 INSERT `AuditLog`
- 結構化日誌用 `logger.info("event.name", extra={...})`；不要把祕密塞進 extra（會自動 redact 但不要試）
- 健康檢查永遠呼叫 `/health/ready`，liveness 用 `/health/live`
- 新 router 註冊走 `main.py` 的冪等 `include_router`（重複前綴會被跳過）

### 測試
- 路由整合測用 `tests/conftest.py` 的 `client` fixture（`httpx.AsyncClient` + `ASGITransport`，**不要** sync `TestClient`，會 event loop 衝突）
- 每測一個 SAVEPOINT、跑完 rollback、DB 永遠乾淨
- 測試查詢要 scope 到 fixture 的 `seed_tenant`/`seed_store`，**不要**全表掃描（會撞 seed/demo 資料）
- 純函式 calc 測放 `tests/services/`，背景任務測放 `tests/jobs/`，router 測放 `tests/routers/`
- 跑前若 DB 髒了：`make db-truncate`

### 蜂群任務
- DevSwarm Coder 輸出**一個** module 檔 + **一個** test_module 檔，不要多檔
- 蜂群產出落地走 `make promote TASK=<id>`，純計算進 `services/calc/`
- system prompt 改了要 bump `devswarm/prompts/_versions.py`（MAJOR/MINOR/PATCH，見檔內說明）
- 跑任務前先想 spec 寫完整、AC ≥ 10、out-of-scope 列清
- 一個任務預算 USD < $5；月總額 < $50

### Git
- 永遠在指定 feature 分支（目前 session 的分支見 session 指示），不要 push 到 main 或 force push
- `.env` 永遠不 commit（gitignore + pre-commit secrets guard 已擋）
- workspace/ 永遠不 commit
- commit message 用 `feat:` / `fix:` / `docs:` / `chore:` / `test:` 前綴

---

## 經常踩到的坑（解法已就位）

### 「Future attached to different loop」
測試用了 sync `TestClient`。改用 `httpx.AsyncClient` + `ASGITransport`（已在 conftest）。

### 「order not found」即使剛剛 POST 成功
service 只 `flush()` 沒 `commit()`。`api/deps.py::get_db` 已加 commit-on-success。

### Test 通過個別跑、合在一起失敗
DB 有殘留資料。`make db-truncate` 清掉。

### Alembic autogenerate 失敗
DB 沒起：`sudo service postgresql start`（SessionStart hook 通常已幫你起好）。

### pyright 報 alembic op/context unknown
正常 — alembic 用 runtime injection。`pyproject.toml` 已 exclude `restaurant_api/alembic/versions`。

### ruff 抱怨中文標點
demo / seed 腳本已加 `RUF001` per-file ignore。

### migration 含危險操作被 CI 擋
`make migration-safety` 會掃 DROP COLUMN / NOT NULL 等。strict 模式（CI）連 MEDIUM 也 fail。

---

## 我希望 Claude Code 怎麼做事

1. **動手前先看 `docs/06_execution_plan.md`** 確認還沒做什麼、Phase 排在哪
2. **改 schema 前先讀 `docs/04_data_schema.md`** 看設計理由
3. **動 router 前先讀 `restaurant_api/api/deps.py` 和 `errors.py`** 用既有 DI/例外
4. **新增測試前先看 `tests/conftest.py`** 用既有 fixture
5. **跑 DevSwarm 前**：確認 `.env` 有 `ANTHROPIC_API_KEY`、用 `--budget` 旗標
6. **commit 前**永遠跑 `make full-check`（ruff + pyright + pytest + alembic drift + db-smoke）
7. **多檔大改用 sub-agent 平行**（看 `.claude/agents/` 有什麼角色）
8. **長 session 超過 50K token 用 `/compact`**，保留重要決策與 spec contract
9. **規劃新功能用 Plan Mode**（Shift+Tab×2），讓我先審計畫再動工
10. **遇到不確定時用 AskUserQuestion**，給我 2-4 個具體選項，不要長段問題

---

## 直接交付指令對照表

| 我說 | 你做 |
|---|---|
| 「跑 demo」 | `make demo`（DevSwarm 跑 specs/profit_calc.md，需 API key） |
| 「跑全部 spec」 | for s in specs/*.md; do make swarm REQ="$(cat $s)"; done |
| 「補 spec」 | 用 `.claude/agents/spec-writer`（或 `/spec`） |
| 「加 router」 | 用 `.claude/agents/router-implementer` |
| 「全綠檢查」 | `make full-check`（或 `/check`） |
| 「狀態」 | `make status` + `make backlog` |
| 「seed」 | `make seed`（或 `make seed-reset` 砍掉重灌） |
| 「跑一日流程」 | `make demo-flow` |
| 「起 API」 | `make api`（dev、auto-reload） |
| 「跑背景任務」 | `make jobs`（常駐）或 `make jobs-once`（cron） |
| 「匯出 OpenAPI」 | `make openapi` |
| 「審查目前 diff」 | 用 `/code-review` skill |
| 「找 bug」 | 用 `/bugfix` skill |
| 「文件交接」 | 看 `COMMANDER_HANDOFF.md`（或 `/handoff`） |
| 「餐飲領域問題」 | 用 `.claude/agents/restaurant-domain-expert` |

---

## 永遠不做

- 把 DevSwarm 指向 production credentials
- 在 main 分支 force push
- 跳過 ruff / pyright / pytest 任何一個 gate
- 用 `# noqa` 掃過真實 lint 錯誤（修根因）
- 改 ledger 表的 DDL 取消 append-only rule
- 把 `.env` 加進 git
- 為了 demo 通過就調寬沙盒超時或預算
- 在 conftest 用 sync TestClient
- 在 service 裡 `session.commit()`（commit 在 DI 層）
- 在 `services/calc/` 裡碰 DB 或 HTTP（破壞純函式契約）
