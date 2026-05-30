# CLAUDE.md — 專案記憶

> 這個檔案是 Claude Code 每次開新 session 自動載入的「專案說明書」。
> 不要把臨時筆記寫在這 — 用 `docs/` 或 `MORNING_BRIEF.md`。
> 這份只放**會影響每次決策**的長期資訊。

---

## 一句話這是什麼

**台灣全域 AI 餐飲智慧營運作業系統**。雙層架構：
- **DevSwarm**（LangGraph 4-agent 蜂群）→ 自動產出程式碼
- **RestSwarm**（FastAPI + 25-table PG + LINE）→ 真實餐飲後端

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
| 驗證 | Pydantic v2 | ≥2.5 |
| HTTP client | httpx | 0.28 |
| LLM SDK | anthropic | 0.104 |
| Agent 編排 | LangGraph | 0.6 |
| Lint | ruff | latest |
| Type check | pyright | basic mode |
| Test | pytest + pytest-asyncio | 8.x / 0.24 |
| 包管理 | pip + pyproject.toml | editable install |

虛擬環境永遠在 `.venv/`。所有指令前綴 `.venv/bin/`。

---

## 目錄結構

```
.
├── CLAUDE.md                    # ← 你正在讀
├── COMMANDER_HANDOFF.md         # 指揮官 to-do 清單
├── MORNING_BRIEF.md             # 早晨速覽（v1 偏舊）
├── Makefile                     # 27 個 make 目標，必看 `make help`
├── pyproject.toml               # 依賴 + ruff + pyright + pytest 全配置
│
├── .claude/                     # Claude Code 專案配置
│   ├── settings.json            # hooks + permissions
│   ├── commands/                # 自訂 slash commands
│   └── agents/                  # 正式 subagent 定義
│
├── devswarm/                    # AI 蜂群本體（2,500 LOC）
│   ├── cli.py / __main__.py     # 入口
│   ├── graph.py                 # LangGraph 拓撲
│   ├── state.py                 # SwarmState TypedDict
│   ├── config.py                # 模型選擇 + 計價
│   ├── llm.py                   # Anthropic SDK wrapper (prompt cache)
│   ├── sandbox.py               # pytest subprocess
│   ├── workspace.py             # 沙盒檔案系統
│   ├── prompts/                 # 4 agent 系統提示 + 版本登記
│   └── nodes/                   # 4 agent node 實作
│
├── restaurant_api/              # Phase 1 餐飲後端（5,200 LOC）
│   ├── main.py                  # FastAPI app + 路由註冊
│   ├── config.py                # Pydantic Settings
│   ├── database.py              # async engine + session
│   ├── docker-compose.yml       # PG + Redis（開發用）
│   ├── alembic/                 # 3 份遷移
│   ├── models/                  # 25 表 SQLAlchemy（含 audit_log、embeddings）
│   ├── api/                     # deps.py + errors.py（共用 DI / 例外）
│   ├── schemas/                 # Pydantic 請求/回應（每 router 一檔）
│   ├── services/                # 業務邏輯（純 async，無 HTTP）
│   ├── routers/                 # FastAPI APIRouter（每模組一檔）
│   └── integrations/line/       # LINE 統一通道（Stub + HTTP skeleton）
│
├── docs/                        # 10 份戰略文件
│   ├── 00_vision.md             # SSOT
│   ├── 01_tech_stack_recommendation.md
│   ├── 02_devswarm_architecture.md
│   ├── 03_roadmap.md            # Phase 0-5
│   ├── 04_data_schema.md        # 909 行 DDL + mv_daily_pnl 損益視圖
│   ├── 05 (空)
│   ├── 06_execution_plan.md     # 12 任務 + 4 決策路徑
│   ├── 07_devswarm_runbook.md   # `make demo` 故障排除
│   ├── 08_safety_compliance.md  # 食安/勞檢/個資/災難 SOP
│   ├── 09_phase1_extension_kit.md  # KDS / 訂位 / LINE 設計
│   └── 10_claude_code_workflow.md  # 何時用什麼 Claude Code 能力
│
├── specs/                       # 10 份 DevSwarm 任務簡報
│   ├── profit_calc.md           # 真實損益（demo 用）
│   ├── uniform_invoice_validator.md
│   ├── bom_consumer.md
│   ├── discount_resolver.md
│   ├── cogs_variance_detector.md
│   ├── labor_hours_classifier.md
│   ├── orders_router.md         # 已實作
│   ├── stock_intake_router.md   # 已實作
│   ├── clock_router.md          # 已實作
│   └── cost_events_router.md    # 已實作
│
├── scripts/                     # 操作腳本
│   ├── backlog.py               # `make backlog` 列出所有 spec 狀態
│   ├── promote.py               # `make promote TASK=<id>` 搬蜂群產出
│   ├── seed_demo_data.py        # `make seed` 灌測試餐廳
│   ├── demo_flow.py             # `make demo-flow` 跑完整 POS 一日
│   └── smoke_db.py              # `make db-smoke` DB 端到端驗證
│
├── tests/                       # 106 個 pytest
│   ├── conftest.py              # DB savepoint fixtures + AsyncClient
│   ├── routers/                 # 37 router 整合測（real PG）
│   ├── test_state.py
│   ├── test_workspace.py
│   ├── test_sandbox.py
│   ├── test_graph_mock.py       # mocked end-to-end DevSwarm
│   ├── test_line_integration.py
│   ├── test_restaurant_api.py
│   └── test_imports.py
│
└── workspace/                   # DevSwarm 產出（gitignored）
```

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
- Pydantic 輸入 `model_config = ConfigDict(frozen=True)` + 用 `BeforeValidator` 拒絕 float（不要用 `strict=True` 會擋 JSON UUID 字串）
- Domain 例外用 `restaurant_api/api/errors.py` 的 `DomainError` 系列，不要用 raw `HTTPException`

### 測試
- 路由整合測用 `tests/conftest.py` 的 `client` fixture（`httpx.AsyncClient` + `ASGITransport`，**不要** sync `TestClient`，會 event loop 衝突）
- 每測一個 SAVEPOINT、跑完 rollback、DB 永遠乾淨
- 測試查詢要 scope 到 fixture 的 `seed_tenant`/`seed_store`，**不要**全表掃描（會撞 seed/demo 資料）
- 跑前若 DB 髒了：`make db-truncate`

### 蜂群任務
- DevSwarm Coder 輸出**一個** module 檔 + **一個** test_module 檔，不要多檔
- system prompt 改了要 bump `devswarm/prompts/_versions.py`
- 跑任務前先想 spec 寫完整、AC ≥ 10、out-of-scope 列清
- 一個任務預算 USD < $5；月總額 < $50

### Git
- 永遠在 `claude/autonomous-resttech-enterprise-oW9jp` 分支
- 不要 push 到 main 或 force push
- `.env` 永遠不 commit（gitignore 已擋）
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
DB 沒起：`sudo service postgresql start`。

### pyright 報 alembic op/context unknown
正常 — alembic 用 runtime injection。`pyproject.toml` 已 exclude `restaurant_api/alembic/versions`。

### ruff 抱怨中文標點
demo / seed 腳本已加 `RUF001` per-file ignore。

---

## 我希望 Claude Code 怎麼做事

1. **動手前先看 `docs/06_execution_plan.md`** 確認還沒做什麼、Phase 排在哪
2. **改 schema 前先讀 `docs/04_data_schema.md`** 看設計理由
3. **動 router 前先讀 `restaurant_api/api/deps.py` 和 `errors.py`** 用既有 DI/例外
4. **新增測試前先看 `tests/conftest.py`** 用既有 fixture
5. **跑 DevSwarm 前**：確認 `.env` 有 `ANTHROPIC_API_KEY`、用 `--budget` 旗標
6. **commit 前**永遠跑 `make full-check`（ruff + pyright + pytest + alembic + smoke）
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
| 「補 spec」 | 用 `.claude/agents/spec-writer` |
| 「加 router」 | 用 `.claude/agents/router-implementer` |
| 「全綠檢查」 | `make full-check` |
| 「狀態」 | `make status` + `make backlog` |
| 「seed」 | `make seed` |
| 「跑一日流程」 | `make demo-flow` |
| 「審查目前 diff」 | 用 `/code-review` skill |
| 「找 bug」 | 用 `/bugfix` skill |
| 「文件交接」 | 看 `COMMANDER_HANDOFF.md` |

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
