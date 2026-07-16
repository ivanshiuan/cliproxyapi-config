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
├── restaurant_api/              # Phase 1 餐飲後端（6,500+ LOC）
│   ├── main.py                  # FastAPI app + 路由註冊 + middleware
│   ├── config.py                # Pydantic Settings
│   ├── database.py              # async engine + session
│   ├── docker-compose.yml       # PG + Redis（開發用）
│   ├── docker-compose.production.yml  # 真實上線 stack
│   ├── Dockerfile               # multi-stage、non-root、tini PID-1
│   ├── alembic/                 # 3 份遷移
│   ├── models/                  # 25 表 SQLAlchemy（含 audit_log、embeddings）
│   ├── middleware/              # RequestContext + 結構化 JSON 日誌
│   ├── api/                     # deps.py + errors.py + health.py（/live + /ready）
│   ├── schemas/                 # Pydantic 請求/回應（每 router 一檔）
│   ├── services/                # 業務邏輯（純 async，無 HTTP；含 audit_service）
│   ├── routers/                 # FastAPI APIRouter（每模組一檔）
│   ├── jobs/                    # APScheduler 背景任務（expiry/points/COGS）
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
│   ├── 10_claude_code_workflow.md  # 何時用什麼 Claude Code 能力
│   └── 11_production_deployment.md # Docker / Cloudflare / 部署 SOP
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
- 寫稽核紀錄一律走 `services/audit_service.audit()`，不要直接 INSERT `AuditLog`
- 結構化日誌用 `logger.info("event.name", extra={...})`；不要把祕密塞進 extra（會自動 redact 但不要試）
- 健康檢查永遠呼叫 `/health/ready`，liveness 用 `/health/live`

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

### PR 工作流（Ivan 的預設 — 不用每次問）
- **一個任務完整做完 + 測試綠了 → 自動開 PR**，不用再問 Ivan「要不要開」。
- PR 標題寫人話、內文寫「改了什麼 / 為什麼 / 怎麼驗證」，給看結果不看程式碼的指揮官審核。
- **不開 PR 的情況**：半成品/進行中、純問答查資料不改檔、大任務拆成多步（全部做完**合成一個** PR，不要每步一個）。
- Ivan 在 GitHub（手機或電腦）審核後按 Merge 進 main；要改就在同個 PR 上修。
- 例外：Ivan 明講「先不要開 PR / 留分支就好」時就不開。

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

## 檔案攝取 — 看到檔案自己選武器（不用我下指令）

> 目的：Ivan 丟檔案進來，Claude **自動**判斷怎麼讀最省 token、結構最乾淨，
> 不要每次問、也不要 Ivan 記指令。規則如下，遇到就照做、不用確認。

**判斷流程（拿到一個檔案路徑時）：**

1. 先看副檔名與大小，對照下表決定「直接 Read」還是「先轉 Markdown 再 Read」。
2. 若要轉：跑 `uv run scripts/to_md.py <檔>`（或 `make to-md FILE=<檔>`），
   它會在原檔旁產生 `<檔>.md`，然後你 Read 那個 `.md`。
3. markitdown 走 `uv run` 隔離環境（PEP 723），**不進 pyproject.toml、碰不到 .venv**。
   首次執行會下載、之後快取秒級。離線無 API key 也能跑（OCR 才需 `--llm`）。

| 檔案 | 怎麼處理 | 為什麼 |
|---|---|---|
| `.md` `.txt` `.csv` `.json` `.xml` `.py` 等純文字 / 小 PDF（≲20 頁文字型） | **直接 `Read`** | 原生就讀得好，轉了沒意義 |
| 大型 PDF（幾十～幾百頁、文字型） | **先 `to_md.py` 轉**再 Read `.md` | 省大量 token、避免分批 |
| Office 檔 `.docx` `.pptx` `.xlsx` `.epub` | **先 `to_md.py` 轉** | Read 不直接吃這些；轉完表格/標題結構乾淨 |
| 複雜 HTML（多層表格） | **先 `to_md.py` 轉** | 去殼留結構，省 token |
| 掃描檔 / 照片型發票（需 OCR） | 先用 `Read` 看圖；要抽文字再 `to_md.py --llm`（需 `OPENAI_API_KEY`） | 一般「看」用原生即可 |

**鐵律**：`to_md.py` 只做「檔案→文字」。**任何要入帳的金額/品項，仍須走
`restaurant_api` 結構化驗證 + 人工覆核，不可直接信轉出來的純文字**（呼應
「金錢永遠不用 float、ledger 可稽核」法則）。

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
| 「讀這個檔 / 我丟 PDF·Office·大檔給你」 | 照「檔案攝取」規則自動判斷：直接 Read 或先 `make to-md FILE=<檔>` 轉再讀 |
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
| 「建檔 / digest / 整理這篇 / 幫我消化 / 存進知識庫 / 把 XX 學起來」+ 一條連結或內容 | 用 `digest` skill → 提煉成知識卡存 `docs/knowledge/` + 登進 `00_MOC.md` |
| 「幫我開網站 / 登入抓資料 / 監看競品菜單價格 / 填沒 API 的後台表單 / 過驗證碼」 | 用 `browser-act` skill（需先開網路白名單，見 `docs/18`） |
| 「帝國大腦怎麼運作 / 知識庫架構 / 格局圖」 | 看 `docs/19_empire_brain.md`；vault 入口 `docs/knowledge/00_MOC.md` |
| 「早安 / 開工 / 今天狀況」 | 用 `/morning` command（起 DB、跑測試、列 backlog、亮 blocker） |
| 「交接 / 收工 / 記錄進度」 | 用 `/handoff` command（更新 COMMANDER_HANDOFF.md） |
| 「跑蜂群做 XX」 | 用 `/swarm` command（帶預算守門） |
| 「部署 SID / 網站上線」 | 用 `deploy-sid` skill |
| 「分析這場球 / 賽前 memo / 盤口」 | 用 `match-intel` skill（僅分析，不推薦下注） |
| 「賽後覆盤 / 準備比賽輸入」 | 用 `tiger-pm` agent |
| 「食安 / 勞基法 / 發票 / 對帳的領域問題」 | 用 `restaurant-domain-expert` agent |
| 「polish / audit / critique 這個 UI / 這個設計 AI 感太重 / 抓 anti-pattern」 | 用 `impeccable:impeccable` skill（前端設計品味審查） |
| 「先想清楚再動手 / 用 Superpowers 流程做 XX / brainstorm 這個功能 / 幫我寫實作計畫」 | 用 `superpowers:brainstorming` → `superpowers:writing-plans` → `superpowers:executing-plans` 三段式（動大功能前先審計畫） |
| 「TDD 做這個 / 寫測試先 / 系統性 debug 這個 bug / 完成前先驗證」 | 用對應 `superpowers:*` 子技能：`test-driven-development` / `systematic-debugging` / `verification-before-completion` |
| 「上次我們討論過的 XX / 之前解過的問題 / 幫我找過去的 session」 | 用 `claude-mem:mem-search` skill（僅在同一容器 / 已設 cloud sync 時有效） |

**Plugin 資料持久化警告**：impeccable / claude-mem / superpowers 三個 plugin 靠 `.claude/hooks/session-start-plugins.sh` 在每個新 remote 容器自動重裝(user-scope 檔案容器回收會消失)。但 **claude-mem 的記憶庫本身也存 `~/.claude-mem/`,同樣會被回收**——目前它只在單一容器內、跨對話有效,容器重啟就重置。要跨容器保留得手動走 `claude-mem:cloud-sync` 設定 cmem.ai Pro 帳號(外部付費服務),Ivan 尚未決定。

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
