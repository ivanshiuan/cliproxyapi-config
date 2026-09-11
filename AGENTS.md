# AGENTS.md — 跨 AI agent 專案總則

> 給**任何**在這個 repo 工作的 AI agent(Claude Code / OpenAI Codex / DeepSeek /
> 豆包 Doubao / Cursor / Cline / …)。這是「開門先讀」的通用入口。
> Claude Code 另外會讀更完整的 `CLAUDE.md`;兩份規則一致,衝突時以 `CLAUDE.md` 為準。

## 這是什麼

台灣餐飲 AI 智慧營運系統:FastAPI + 25 表 PostgreSQL + LINE 通道(`restaurant_api/`),
外加一個 LangGraph 蜂群(`devswarm/`)。完整願景見 `docs/00_vision.md`、目錄見 `README.md`。

## 鐵律(每個 agent 都要遵守)

- **金錢一律用 `Decimal`,永遠不要 `float`**;ORM money 欄位用 `Numeric(14, 4)`。
- Ledger 表(`stock_movements` / `audit_log` / `customer_points_ledger`)**append-only**,
  不要改成可 UPDATE/DELETE(DB-level RULE 已擋)。
- **`.env` 永不 commit**;需要的祕密(帳密、金鑰)走環境變數 / `.env`,不要寫進程式或對話。
- 分支:在 `claude/*` feature 分支開發,**不要 push 或 force-push 到預設分支**;
  由人類審 PR 後才 merge。
- commit 前跑 `make full-check`(ruff + pyright + pytest + alembic + smoke)。
- **交付方式(Ivan 的鐵則)**:任何操作做成 **idempotent 的 one-command automation**
  (`make <target>` 或 `scripts/<x>.sh`,可重複跑、真的失敗才非零離開);
  **不要丟 runbook / 手動步驟清單給人執行**,人類只負責最終 approval(審 PR、按 Merge)。

## WrenAI —— 用中文問資料 → 受治理 SQL(語意化 text-to-SQL / GenBI)

跟哪個 AI 無關:`wren` 是終端機 CLI,任何能跑 shell 的 agent 都能用。

- **一鍵(冪等)**:`make wren` → 裝 CLI + 建 demo 資料 + 編譯 MDL 語意層 + 跑驗證查詢。
- CLI 裝法:`scripts/setup_wren.sh`(或 `make wren-install`);執行檔在 `~/.local/bin`,**不進版控**。
- 工作流程指南**活在 CLI 裡**(永遠跟版本同步):`wren skills list`、`wren skills get <name>`。
- 文件:`docs/20_wrenai_setup.md`;可跑範例專案:`wren/demo/`(見其 `README.md`)。
- 技能檔:`.claude/skills/wren/`(Claude Code 自動載入)、`.agents/skills/wren/`(跨 agent 共用正本)。

## 更多

- 完整規則、目錄導覽、常踩的坑:`CLAUDE.md`。
- 戰略文件:`docs/`(00 願景 → 20 WrenAI)。
