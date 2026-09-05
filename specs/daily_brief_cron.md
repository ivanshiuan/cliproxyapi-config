# Spec: Daily Brief Cron (`jobs.daily_brief`)

> **Module name:** `restaurant_api.jobs.daily_brief`
> **Owner domain:** AI / Knowledge / Commander's daily ops
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** APScheduler-driven async jobs (in-process,
> per existing `jobs/__init__.py` pattern)
> **Depends on:**
> - `specs/knowledge_schema.md` (3 tables + Alembic migration shipped)
> - `specs/knowledge_ingestion.md` (knowledge base populated; ≥ 1 doc per scope)
> - `services.knowledge_retrieval` (Protocol declared here; real impl is a
>   later spec — tests inject a Fake)
> - `services.llm_client` (Protocol declared here; real Anthropic wrapper
>   is a later spec — tests inject a Fake)
> **Files added:**
> - `restaurant_api/jobs/daily_brief.py`
> - `restaurant_api/jobs/_brief_prompts.py`     (Traditional-Chinese templates, versioned)
> - `restaurant_api/jobs/_brief_sinks.py`       (FileSink / NullSink)
> - `restaurant_api/models/brief.py`            (1 new table: `brief_runs`)
> - `restaurant_api/alembic/versions/<ts>_brief_runs.py`
> - `tests/jobs/test_daily_brief.py`

---

## 1. Background

周霸虎還沒開門。在 POS 沒接通、沒有真實訂單流量之前，「自動化收米」不可能。**但「自動化把雜事消化掉，讓 commander 專心開店」可以**。本 spec 是 BUFF OS 自動化的第一階段：4 個排程任務，每個都從知識軍火庫拉資料、過 LLM、產出可讀的 markdown brief，commander 早上打開就能看，**不自動發給任何人**。

設計鐵則（直接抄入碼）：

- **Proposal, not action**：4 個 brief 的輸出全部寫到 repo 內的 markdown 檔（`MORNING_BRIEF.md`、`ENGINEERING_GAPS.md`、`INVESTOR_QA_PREP.md`、`WEEKLY_REVIEW.md`）。Notion / Drive / Email / LINE 全部 **out of scope**，留給後續 spec。
- **Idempotent within calendar day**：同一個 brief 在同一個業務日重複執行 → 短路回傳「已執行」，不重跑 LLM。
- **Versioned prompts**：每個 prompt template 是模組層級常數 + 版本號。改 prompt → bump 版本 → 重新跑會被視為新 run。
- **Audit everything**：每次成功跑、失敗、或被短路，寫一筆 `audit_log`。
- **Empty-knowledge-base graceful**：若知識庫對應 scope 沒有資料，brief 不報錯，輸出「scope X 尚無資料，請先 ingest」並照樣寫檔。

---

## 2. Goal

提供 4 個獨立、可分別開關的 cron job：

| Job id | Cron (Asia/Taipei) | Output file | 觀眾 |
|---|---|---|---|
| `today_top5`         | `09:00 *`        | `MORNING_BRIEF.md`     | commander（每日） |
| `engineering_gaps`   | `18:00 *`        | `ENGINEERING_GAPS.md`  | commander + 包商溝通用 |
| `investor_qa_prep`   | `09:00 Mon`      | `INVESTOR_QA_PREP.md`  | commander（投資人會議前） |
| `weekly_review`      | `17:00 Fri`      | `WEEKLY_REVIEW.md`     | commander 週末檢討 |

每個 job：
1. 從 `knowledge_*` 表撈相關 scope 的最近 N 篇文件 + chunks。
2. 套上 versioned prompt template，呼叫 `LLMClient.complete()` 拿回結構化 brief。
3. 寫到對應 markdown 檔（`FileSink`）。
4. 寫 1 筆 `brief_runs`、N 筆 `knowledge_queries`、1 筆 `audit_log`。
5. 回傳 `BriefRunResult`（給 scheduler logger）。

---

## 3. Scope

### 3.1 In scope

- 4 個 `run_X()` async function（皆無參數預設 + 可選 `session` 與 `now` for tests）
- 1 個共用 `_run_brief(kind, ...)` helper（DRY 4 job 的共同骨架）
- `_brief_prompts.py`：4 個 prompt template 常數 + 版本號（Traditional Chinese）
- `_brief_sinks.py`：`BriefSink` Protocol + `FileSink` + `NullSink` 兩個實作
- `brief_runs` 表 + Alembic migration
- 與既有 `jobs/__init__.py` 的 scheduler wiring 整合（patch 4 個新 job 進去）
- `tests/jobs/test_daily_brief.py` 涵蓋 17 條 AC
- 一份 ≤ 30 行 quickstart `restaurant_api/jobs/README.md`（**僅**追加 daily_brief 一節，不重寫整份）

### 3.2 Out of scope（延後）

- Notion sink、Drive sink、Email sink、LINE sink → 後續 spec
- 真實 `LLMClient` 實作（本 spec 只定 Protocol，注入 `FakeLLMClient`）
- 真實 `RetrievalClient` 實作（同上）
- 自動發出去給任何外部對象（投資人、員工、包商）— **永遠 out of scope**
- Brief 結果的 web dashboard / 行動裝置 UI
- Multi-tenant 跨租戶聚合（本 spec 是 single-tenant-per-run，commander 的 tenant）
- 即時觸發 brief（HTTP `POST /briefs/run` API）→ 後續 spec
- LLM cost tracking（先記 `prompt_tokens` / `completion_tokens` 在 `brief_runs`，定價算後續）
- 多語輸出（只輸出 Traditional Chinese）

---

## 4. The 4 briefs (concrete)

### 4.1 `today_top5` — 今日 5 件最重要

| 維度 | 設定 |
|---|---|
| Cron | `09:00 *` Asia/Taipei |
| Scope filter | `[funding, engineering, sop, subsidy]`（4 個主戰場） |
| Retrieval k | 每 scope 取 top-15 chunks（共 60） |
| LLM model | 由 `LLMClient` 預設決定（spec 不綁） |
| Output sink | `FileSink("MORNING_BRIEF.md")`（覆寫） |
| Prompt template | `PROMPT_TODAY_TOP5_V1`（見 §6） |
| Idempotency key | `(tenant_id, 'today_top5', business_date)` |

**輸出形狀（markdown）：**

```markdown
# 今日重點 — 2026-06-27（週六）

## TL;DR
（一段 ≤ 80 字的人話總結）

## 今日 5 件事（依優先級）

### 1. [funding] 跟張先生回覆估值 1350 vs 1400 的差別
- **為什麼今天**：他週四問了還沒回。
- **要做什麼**：用 BP V8.1 §3.2 的數字回 1 段 ≤ 200 字。
- **引用**：`knowledge_documents/<id>` BP_V8.1.pdf, §3.2

### 2. [engineering] ...

...
```

### 4.2 `engineering_gaps` — 工程缺項與比價

| 維度 | 設定 |
|---|---|
| Cron | `18:00 *` |
| Scope filter | `[engineering]` |
| Retrieval k | top-40 chunks |
| Output sink | `FileSink("ENGINEERING_GAPS.md")` |
| Prompt template | `PROMPT_ENGINEERING_GAPS_V1` |
| Idempotency key | `(tenant_id, 'engineering_gaps', business_date)` |

**輸出形狀**：以表格為主——「項目 / 已報價 / 缺項 / 異常 / 該問誰」四欄。

### 4.3 `investor_qa_prep` — 投資人 Q&A 預測

| 維度 | 設定 |
|---|---|
| Cron | `09:00 Mon` |
| Scope filter | `[funding]` |
| Retrieval k | top-50 chunks |
| Output sink | `FileSink("INVESTOR_QA_PREP.md")` |
| Prompt template | `PROMPT_INVESTOR_QA_V1` |
| Idempotency key | `(tenant_id, 'investor_qa_prep', business_date)` |

**輸出形狀**：10 個「接下來會被問什麼 → 我們的依據答案 → 引用」三欄。

### 4.4 `weekly_review` — 一週做了什麼、下週重點

| 維度 | 設定 |
|---|---|
| Cron | `17:00 Fri` |
| Scope filter | `[funding, engineering, sop, brand, compete, subsidy, system]`（全） |
| Retrieval k | 每 scope top-10（共 70） |
| Extra context | 本週 `audit_log` 的 `action LIKE 'knowledge.%'` 行數 + brief_runs 摘要 |
| Output sink | `FileSink("WEEKLY_REVIEW.md")` |
| Prompt template | `PROMPT_WEEKLY_REVIEW_V1` |
| Idempotency key | `(tenant_id, 'weekly_review', business_date_friday)`（key 鎖定週五業務日） |

**輸出形狀**：兩段——「本週做了什麼（依戰場）」+「下週 3 件最重要」。

---

## 5. Pydantic schemas

所有 input model：`ConfigDict(frozen=True, strict=True)`。

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


BriefKind = Literal[
    "today_top5",
    "engineering_gaps",
    "investor_qa_prep",
    "weekly_review",
]


class BriefRunResult(BaseModel):
    """One row's worth of info returned from a brief job."""

    model_config = ConfigDict(frozen=True)

    brief_kind: BriefKind
    tenant_id: UUID
    business_date: date
    status: Literal["completed", "skipped_duplicate", "skipped_empty_kb", "failed"]
    brief_run_id: UUID | None = None    # None if skipped_duplicate without insert
    retrieved_chunk_count: int = 0
    prompt_template_version: str         # e.g. "today_top5/v1"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    output_path: str | None = None       # None if NullSink
    error_message: str | None = None
```

---

## 6. Prompt templates (Traditional Chinese, versioned)

**Rule**: 任何 prompt 文字更動 → 同檔案內 bump suffix（`_V1` → `_V2`），不要原地改。

```python
# _brief_prompts.py

PROMPT_TODAY_TOP5_V1 = """你是周霸虎品牌的執行助理。

收到的 context 包含 {scope_summary}，已從知識庫撈出 {chunk_count} 段資料。

請依以下規則產出今日 brief：

1. 用 Traditional Chinese 輸出 Markdown。
2. 開頭一段 TL;DR ≤ 80 字。
3. 列出 5 件「今天最該做」的事，依優先級（募資 > 現金流 > 工程 > 營運 > 品牌）排序。
4. 每件事三行內：為什麼今天、具體要做什麼、引用 chunk_id。
5. 不要編造數字。若資料不足，寫「依現有資料無法判斷，需先確認 X」。
6. 不要喊口號，不要寫雞湯。
7. 禁用詞：奶油、網美、CP值、爆款、療癒、加油、努力。

今天日期：{today_taipei}
本週是否週末：{is_weekend}
"""

PROMPT_ENGINEERING_GAPS_V1 = """你是周霸虎品牌的工程督導助理。

從 engineering scope 的 {chunk_count} 段資料裡，輸出一份「今日工程缺項與比價」brief。

格式（Markdown 表格）：

| 項目 | 已報價 (TWD) | 缺項 / 異常 | 該問誰 |

規則：
1. 每一列必須引用 chunk_id。
2. 缺項 = 該項目應有報價但目前沒有。
3. 異常 = 同類項目間價格差 > 20%，或單項 > 50 萬未拆細。
4. 若資料不夠判斷，那一列寫「資料不足，需請廠商補」。
5. 表後加一段「今天最該追的 3 件事」≤ 100 字。

今天日期：{today_taipei}
"""

PROMPT_INVESTOR_QA_V1 = """你是周霸虎品牌的募資準備助理。

從 funding scope 的 {chunk_count} 段資料裡，輸出「未來一週投資人最可能問的 10 個問題」與我們的依據答案。

格式（Markdown 表格）：

| # | 預測問題 | 我們的答案（≤ 100 字） | 引用 chunk_id |

規則：
1. 問題要具體（不要「為什麼選火鍋」這種無聊的）。
2. 答案要有數字、有依據；若依據是矛盾的，明寫矛盾在哪。
3. 表後加一段「目前資料最大的 3 個漏洞」≤ 150 字——這是要 commander 補資料的訊號。
4. Traditional Chinese 輸出。

本週日期區間：{week_start_taipei} ~ {week_end_taipei}
"""

PROMPT_WEEKLY_REVIEW_V1 = """你是周霸虎品牌的週末檢討助理。

context 包含全 7 個 scope 的最新資料 + 本週的 audit_log 摘要 + 上週的 brief_runs 摘要。

輸出格式（Markdown）：

## 本週做了什麼
（依戰場分段，每段 ≤ 5 點 bullet。戰場順序：募資、工程、營運、品牌、競品、補助、AI 系統）

## 下週 3 件最重要
1. ...
2. ...
3. ...

## 本週風險訊號（≤ 3 個）
- ...

規則：
1. 「本週做了什麼」必須引用 chunk_id 或 audit_log 條目。
2. 不要編。沒做就寫沒做。
3. 若某戰場本週無進度，那段寫「無進度」一句即可，不要硬擠。
4. Traditional Chinese 輸出。

本週日期區間：{week_start_taipei} ~ {week_end_taipei}（週五）
"""
```

每個 template 的 `_V1` 對應 `prompt_template_version="today_top5/v1"` 等。bump 時：`PROMPT_TODAY_TOP5_V2 = "..."` + 改 `_VERSION_MAP[today_top5] = "today_top5/v2"`。

---

## 7. `brief_runs` table

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | `UUID` (uuidv7) | no | `uuid7()` | PK |
| `tenant_id` | `UUID` | no | — | FK `tenants.id` ON DELETE RESTRICT |
| `brief_kind` | `String(32)` | no | — | one of `BriefKind` values |
| `business_date` | `Date` | no | — | Asia/Taipei calendar date the brief is for |
| `status` | `String(32)` | no | — | one of `BriefRunResult.status` values |
| `prompt_template_version` | `String(64)` | no | — | e.g. `today_top5/v1` |
| `retrieved_chunk_count` | `Integer` | no | `0` | how many chunks we passed to the LLM |
| `prompt_tokens` | `Integer` | no | `0` | LLM accounting |
| `completion_tokens` | `Integer` | no | `0` | same |
| `latency_ms` | `Integer` | no | `0` | retrieval + generation |
| `output_path` | `Text` | yes | `null` | repo-relative; null for `NullSink` |
| `output_markdown` | `Text` | yes | `null` | full brief text (yes, also in file — DB copy is the canonical replay source) |
| `error_message` | `Text` | yes | `null` | if `status='failed'` |
| `created_at` | `timestamptz` | no | `now()` | TimestampedMixin |
| `updated_at` | `timestamptz` | no | `now()` | TimestampedMixin |

**Indexes:**
- PK: `id`
- `ix_brief_runs_tenant_id` (mixin)
- `uq_brief_runs_idempotent`：UNIQUE `(tenant_id, brief_kind, business_date, prompt_template_version)` — 同 prompt 版本一天只跑一次；prompt bump 後可以重跑（新 row）
- `ix_brief_runs_tenant_kind_time`：`(tenant_id, brief_kind, business_date DESC)`

**Constraints:**
- CHECK `brief_kind IN ('today_top5','engineering_gaps','investor_qa_prep','weekly_review')`
- CHECK `status IN ('completed','skipped_duplicate','skipped_empty_kb','failed')`
- CHECK `(status='failed') = (error_message IS NOT NULL)`

**Append-only?** No. 失敗的 row 允許 UPDATE 成 retry 後的 completed 狀態（同 prompt 版本同日，先 failed 後 completed 應該是同一個邏輯 run）。但**不**繼承 SoftDeleteMixin（沒有刪除路徑）。

---

## 8. Sinks

```python
class BriefSink(Protocol):
    async def write(self, *, kind: BriefKind, markdown: str) -> str | None:
        """Persist the brief. Returns the resolved output path, or None if no path
        (e.g. NullSink). Sinks are synchronous-conceptually but expose async for
        consistency with the rest of the codebase."""
        ...


class FileSink:
    """Writes to a repo-relative path. Overwrites on every run."""

    def __init__(self, repo_relative_path: str) -> None: ...
    async def write(self, *, kind: BriefKind, markdown: str) -> str: ...


class NullSink:
    """No-op sink for dry-runs and tests."""
    async def write(self, *, kind: BriefKind, markdown: str) -> None: ...
```

**`FileSink` rules:**
- Path must NOT escape repo root (no `..`); enforce via `resolve()` + prefix check; otherwise raise `BriefSinkError`.
- Atomic write: write to `<path>.tmp` first, then `os.replace`. Avoids half-written briefs.
- File header injected automatically: `<!-- auto-generated by daily_brief.<kind> at <iso ts> -->\n\n`.

---

## 9. Public interface

```python
async def run_today_top5(
    *,
    session: AsyncSession | None = None,
    now: datetime | None = None,
    sink: BriefSink | None = None,
    retrieval: RetrievalClient | None = None,
    llm: LLMClient | None = None,
) -> BriefRunResult: ...

async def run_engineering_gaps(...) -> BriefRunResult: ...
async def run_investor_qa_prep(...) -> BriefRunResult: ...
async def run_weekly_review(...) -> BriefRunResult: ...
```

預設值：
- `session` None → 用 `get_sessionmaker()` 自開 session、自 commit、自關。
- `now` None → `datetime.now(UTC)`；測試傳固定值。
- `sink` None → `FileSink(<output_path_per_kind>)`。
- `retrieval` / `llm` None → 由 `restaurant_api/services/__init__.py` 拉預設實作（**待 Phase 2 spec 寫**；本 spec 階段測試一律注入）。

**Scheduler wiring**（加進 `jobs/__init__.py::_register`）：

```python
scheduler.add_job(
    _wrap("today_top5", run_today_top5),
    trigger=CronTrigger(hour=9, minute=0, timezone=tz),
    id="today_top5",
    max_instances=1, coalesce=True, misfire_grace_time=600,
)
scheduler.add_job(
    _wrap("engineering_gaps", run_engineering_gaps),
    trigger=CronTrigger(hour=18, minute=0, timezone=tz),
    id="engineering_gaps",
    max_instances=1, coalesce=True, misfire_grace_time=600,
)
scheduler.add_job(
    _wrap("investor_qa_prep", run_investor_qa_prep),
    trigger=CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=tz),
    id="investor_qa_prep",
    max_instances=1, coalesce=True, misfire_grace_time=600,
)
scheduler.add_job(
    _wrap("weekly_review", run_weekly_review),
    trigger=CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=tz),
    id="weekly_review",
    max_instances=1, coalesce=True, misfire_grace_time=600,
)
```

---

## 10. Idempotency + retry semantics

| 情境 | 行為 |
|---|---|
| 同一日同 prompt 版本第二次跑 | SELECT `brief_runs` 命中 → 回 `status='skipped_duplicate'`、`brief_run_id=<existing>`、不重跑 LLM、不寫檔 |
| 同一日 prompt 版本 bump 後重跑 | 視為新 run（unique key 含 version）→ 重跑、寫新 row、覆寫檔案 |
| 上一次跑 `status='failed'`、同日 prompt 版本未變 | UPDATE 既有 row 為 retry，不寫新 row（unique key 衝突保證）；成功則 `status='completed'`，失敗則更新 `error_message` |
| 知識庫對應 scope 為空 | `status='skipped_empty_kb'`、output 是「scope X 尚無資料，請先 ingest」、檔案照寫、不呼叫 LLM |
| Misfire（過 10min grace 才到）| APScheduler 自動 coalesce + skip；不在本模組處理 |

**`business_date` 取法**：用 Asia/Taipei 時區的 `(now).astimezone(TPE).date()`。週五 17:00 brief 跑時 `business_date` 就是當週週五的日期；`weekly_review` 的 unique key 用同一個值，週末若 misfire 也不會誤觸下週的 row。

---

## 11. Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_daily_brief_ac_NN_*`。
> 測試用 `tests/conftest.py` 的 `async_session` + `seed_tenant` + 注入 `FakeLLMClient` + `FakeRetrievalClient` + `tmp_path` 為 `FileSink` root。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1  | today_top5 happy path | seed 4 個 scope 各 5 個 chunks、`FakeLLMClient` 回固定 markdown → `status='completed'`、`brief_runs` 1 row、`MORNING_BRIEF.md` 被寫入且開頭含 auto-generated 註解。 |
| AC-2  | Idempotent same day | 連跑 2 次 → 第 2 次 `status='skipped_duplicate'`、`brief_runs` 仍只 1 row、LLM 只被呼叫 1 次。 |
| AC-3  | Prompt bump triggers re-run | 模擬 `_VERSION_MAP['today_top5']='today_top5/v2'`，同日重跑 → 新 row、LLM 第 2 次被呼叫。 |
| AC-4  | Empty KB graceful | 0 個 chunks → `status='skipped_empty_kb'`、檔案內容含「尚無資料」字串、LLM **不**被呼叫、`brief_runs` 1 row。 |
| AC-5  | LLM failure retry then succeed | 第 1 次 LLM 拋 `LLMError` → row `status='failed'` `error_message` 填上；第 2 次跑（同日同版本）UPDATE 該 row 為 `status='completed'`。 |
| AC-6  | engineering_gaps scope filter | `FakeRetrievalClient` 只能收到 `scope=['engineering']` 的呼叫，brief 內容包含表頭「項目 / 已報價 / 缺項 / 異常 / 該問誰」。 |
| AC-7  | investor_qa_prep weekly cron metadata | run 時注入 now=週一 09:00 Asia/Taipei，brief_runs.business_date = 該週一日期；非週一觸發時（測試用任意 datetime）business_date 仍為傳入的 Asia/Taipei date，**不**自動 normalize 到週一（cron 已負責週對齊）。 |
| AC-8  | weekly_review business_date is Friday | now=週五 17:00 Asia/Taipei → `business_date == 該週五`；同邏輯週六 09:00 補跑時 `business_date == 週五`（取 `now.weekday()<=4 ? today : last_friday`）。 |
| AC-9  | weekly_review includes audit_log summary | seed 本週有 5 筆 `action='knowledge.ingested'` 的 audit_log → `FakeLLMClient` 收到的 prompt 內含這個 5 的字串。 |
| AC-10 | FileSink atomic | mock `os.replace` 失敗 → 沒有 `.tmp` 殘留檔；`brief_runs.status='failed'`。 |
| AC-11 | FileSink rejects path escape | sink 初始化用 `../etc/passwd` → 應在初始化就拋 `BriefSinkError`，不要等到 write 時。 |
| AC-12 | NullSink works | 注入 `NullSink` → `output_path=None`、`brief_runs.output_path IS NULL`、`brief_runs.output_markdown` 仍有內容。 |
| AC-13 | audit_log written | 成功 run → `audit_log` 1 筆 `action='brief.completed'`、`target_table='brief_runs'`、`target_id=<row.id>`、`after.brief_kind` 對應。 |
| AC-14 | retrieval audit written | brief 內呼叫 retrieval → `knowledge_queries` 至少 1 筆 `actor_kind='cron'`、`actor_id='daily_brief.<kind>'`、`retrieved_chunk_ids` 非空（empty_kb 場景例外）。 |
| AC-15 | Scheduler registers 4 jobs | 載入 `restaurant_api.jobs` → `scheduler.get_jobs()` 含 `today_top5`、`engineering_gaps`、`investor_qa_prep`、`weekly_review` 四個 id。 |
| AC-16 | Brief never calls outside sinks | 跑任一 brief → 無 `httpx` 呼叫、無 SMTP、無 LINE、無 Notion。Mock `httpx.AsyncClient` 並 assert 它未被建構。 |
| AC-17 | Forbidden words filter (smoke) | brief 輸出含「奶油」/ 「網美」 / 「CP值」/「爆款」 → log warning 並在 `brief_runs.meta` 寫 `forbidden_words: [...]`，但 **不**擋寫入（commander 仍能看到）。 |

---

## 12. Edge cases（必須在測試列舉）

- **knowledge_queries write before LLM call vs after**：spec 規定先 retrieval 再 LLM，但 `knowledge_queries` row 在 LLM call 完成後一次寫入（含 latency / tokens），而非分兩段寫；若 LLM 失敗仍寫入 `was_blocked=false`、`answer_text=null`。
- **`now` 跨日**：job 在 23:59 觸發、retrieval 跨到 00:01，`business_date` 取 **觸發時刻**的 Asia/Taipei date，不是 retrieval 完成時。
- **multi-tenant**：本 spec MVP 假設只有 1 tenant；scheduler 跑時若 DB 有 N 個 tenant，**只跑** `settings.default_tenant_id`（待新增 setting；若未設，拋 `BriefConfigError` fail loud）。多 tenant 排程是後續 spec。
- **Prompt token overflow**：retrieval 拉太多 chunks，prompt 估算 > LLMClient 的 input limit → 截到剛好為止，並在 `brief_runs.meta` 寫 `truncated: true`、`truncated_chunks: N`。
- **Disk full when FileSink writes**：`OSError` → `status='failed'`、`error_message` 填上、不留殘檔。
- **同一個 `tenant_id` 同一天兩個 `business_date`（時區誤用）**：unique 已鎖 (tenant, kind, date, version)；測試模擬 UTC date vs Taipei date 不同的情況，確保用的是 Taipei date。

---

## 13. Constraints（hard requirements）

- Python 3.12，async/await，**禁** sync I/O 在 hot path。
- **金錢**：本 spec 無金錢欄位（LLM cost 後續算）。
- **時間**：Asia/Taipei 由 `settings.default_timezone` 取，**禁**寫死 `"Asia/Taipei"` 字串於 module 層級（只能在 `_brief_prompts.py` 的人類可讀模板裡出現）。
- **Audit**：每筆 success / fail / dup / empty-kb 都走 `audit_service.audit()`。
- **Logging**：用 `logger.info("event.name", extra={...})`；**不准**把 brief 全文進 log（太大）；只進 `brief_runs.output_markdown`。
- **Errors**：`api/errors.py` 新增 `BriefSinkError`、`BriefConfigError`、`LLMError`；後者是 `LLMClient.complete()` 失敗時的統一例外。
- **No raw `HTTPException`**：本 spec 不涉 router。
- **No external network calls**：sinks 只能 file / null；任何 HTTP 客戶端**禁止**從本模組 import。
- **Prompt templates 必須 versioned**：modal 層級常數 + `_VERSION_MAP` dict；ruff 加 per-module 規則確保 `PROMPT_*` 全大寫。
- **Tests**：`tests/conftest.py` 的 `async_session` + `seed_tenant`；**不**用 sync `TestClient`。
- **`make full-check` 全綠**才算 Done。

---

## 14. Out of scope（重申）

- Notion / Drive / Email / LINE sink
- 真實 `LLMClient` / `RetrievalClient` 實作
- HTTP API 即時觸發 brief
- Web dashboard
- 多 tenant 排程
- LLM cost 帳單
- 多語輸出
- 「自動發給投資人」之類的任何外部動作 — **永遠 out of scope**

---

## 15. Connection to other modules

| Module | 介面 |
|---|---|
| `services.audit_service.audit()` | 每 brief run 寫 1 筆 `audit_log` |
| `models.KnowledgeDocument/Chunk/Query` | retrieval + 寫 `knowledge_queries` |
| `models.AuditLog` | 被 `weekly_review` 反查（過去 7 天 `knowledge.%` action 數量） |
| `models.brief.BriefRun` | 本 spec 新增 |
| `jobs.__init__._register` | patch 4 個 add_job 進去 |
| `services.knowledge_retrieval`（後續 spec） | 本 spec 只用 Protocol；真實實作後續 |
| `services.llm_client`（後續 spec） | 同上 |
| `api/errors.py` | 新增 3 個 DomainError subclass |
| `settings` (`config.py`) | 新增 `default_tenant_id: UUID`（若未設值，jobs 啟動 fail loud） |

---

## 16. Done = all of:

1. 5 個檔案就位（`daily_brief.py` + `_brief_prompts.py` + `_brief_sinks.py` + `models/brief.py` + alembic migration），type-checks cleanly、ruff 全綠。
2. `jobs/__init__.py` 已加 4 個 `add_job` 呼叫，`scheduler.get_jobs()` AC-15 通過。
3. `tests/jobs/test_daily_brief.py` 含 AC-1 ~ AC-17 對應 test functions，全綠。
4. `make full-check` 全綠（ruff + pyright + pytest + alembic-check + db-smoke）。
5. 一份 `MORNING_BRIEF.md` 範本（**測試用 fixture**，不 commit 到 repo root；放 `tests/jobs/fixtures/expected_morning_brief.md`）顯示期待輸出長相，作為 prompt 設計檢核點。
6. `restaurant_api/jobs/README.md` 追加 daily_brief 一節（≤ 30 行 quickstart），含「怎麼手動跑一次」+「怎麼開關單一 brief」。
7. `_brief_prompts.py` 內 4 個 prompt 與本 spec §6 逐字一致；變更需同步 spec + bump 版本。

— end of spec —
