# Spec: Employee Pets Router (`/employee-pets`)

> **Module name:** `restaurant_api.routers.employee_pets`
> **Schemas module:** `restaurant_api/schemas/employee_pets.py`
> **Owner domain:** HR / Employee Engagement（養成遊戲化 · pilot 員工單邊）
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched (read/write via service):** `restaurant_api/models/gamification.py`
> （7 表，見 `specs/employee_pet_models.md`）
> **Services called (router 不自己寫業務邏輯):**
> `restaurant_api/services/employee_pet_service.py`、
> `restaurant_api/services/employee_reward_service.py`
> **PRD source of truth:** `docs/18_employee_pet_gamification.md`（附錄 C 員工主畫面草圖）

---

## Background

把員工的考勤／任務／學習換皮成「養一隻電子雞」。本 router 是 PRD Phase A 的 **HTTP 入口**：
員工端讀主畫面（雞狀態＋蛋包餘額＋streak＋階段）、命名雞、餵食、兌換、提交任務、發起真錢兌換、看排行榜；
管理端（店長/老闆）審核待處理的任務完成與真錢兌換。

本 router 是一層**薄殼**：所有業務規則（餘額一致性、升階運算、月池排隊、稽核）都委派給
`employee_pet_service` 與 `employee_reward_service`（比照 `restaurant_api/routers/clock.py` 委派
`clock_service` 的慣例）。router 只負責：解析請求 → 呼叫 service → 回傳 response model。
資料層契約（表名／欄位／enum 值域）一律以 `specs/employee_pet_models.md` 為準，本 spec 不得另立值域。

**核心合規**（呼應 PRD D1）：紅蛋／生病／活力衰退只影響遊戲內數值，**絕不**連結真實薪資；
真錢只從 `employee_reward_pool` 正向發放、需審核、有月池上限。

---

## Routes

| Method | Path | Audience | Purpose |
|---|---|---|---|
| `GET`  | `/employee-pets/me` | employee | 主畫面：雞狀態＋蛋包餘額＋streak＋階段 |
| `POST` | `/employee-pets` | employee | 首次 onboarding：建立並命名雞（一員工一雞） |
| `POST` | `/employee-pets/feed` | employee | 餵蛋（升級）或餵飼料（維持健康） |
| `POST` | `/employee-pets/exchange` | employee | 蛋升階兌換（白→銀→金） |
| `GET`  | `/employee-pets/tasks` | employee | 今日任務清單（含完成狀態） |
| `POST` | `/employee-pets/tasks/{task_id}/complete` | employee | 提交任務完成（可帶 evidence_url） |
| `POST` | `/employee-pets/redemptions` | employee | 發起真錢兌換（金蛋/帝王雞/月目標） |
| `GET`  | `/employee-pets/leaderboard` | employee | 本店排行榜（scope 到 store） |
| `GET`  | `/employee-pets/admin/pending` | **admin** | 待審任務完成 ＋ 待審兌換 |
| `POST` | `/employee-pets/admin/completions/{completion_id}/review` | **admin** | 核可/退回任務（approve→經 pet service 發蛋）|
| `POST` | `/employee-pets/admin/redemptions/{redemption_id}/review` | **admin** | 核可/退回/標記已付兌換 |

所有路由 prefix：`/employee-pets`；OpenAPI tag：`employee-pets`。
所有路由都注入 `session: AsyncSession = Depends(get_db)`（用 `restaurant_api/api/deps.py::DbSession` 別名）。
`employee_id` / `store_id` 在 pilot 由 request body / query 帶入（Phase 2 改由 gateway 認證注入，見 Out of scope）。
標 **admin** 的 3 條路由是 **admin-only**：授權邊界 Phase 2 由上游 gateway 處理（比照 `orders_router` 的
authn/authz out-of-scope 寫法），本 spec 只在表中註明哪些路由需要 admin。

### GET /employee-pets/me

| Query field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must own a pet（否則 404） |

**Behaviour:** 呼叫 `employee_pet_service.get_pet_dashboard(session, employee_id)`，回傳
`PetDashboardResponse`（雞狀態＋四種蛋餘額＋飼料數＋streak＋階段）。404 if 該員工尚未建立雞。

### POST /employee-pets

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must exist in `employees` |
| `store_id` | `UUID` | yes | — | must exist in `stores` |
| `name` | `str` | yes | — | 1..50 chars（雞的名字） |

**Behaviour:** 呼叫 `employee_pet_service.create_pet(...)`。一員工一雞：若該
`(tenant_id, employee_id)` 已有雞 → service 拋 `ConflictError` → **409**。
成功 → **201** + `PetDashboardResponse`，預設 `stage='chick'`、`level=1`、`health=100`、`vitality=100`、餘額全 0。

### POST /employee-pets/feed

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must own a pet |
| `mode` | `Literal["egg","food"]` | yes | — | closed set（`egg`=餵蛋升級 / `food`=餵飼料維持）|
| `egg_type` | `EggType \| None` | no | `None` | `mode=="egg"` 時必填；white/silver/gold（red 不可餵，422）|
| `amount` | `int` | no | `1` | `>= 1` |

**Behaviour:** 呼叫 `employee_pet_service.feed_pet(session, employee_id, mode, egg_type, amount)`。
service 檢查蛋/飼料餘額是否足夠（不足 → `ValidationError` → 422）、寫 `employee_pet_care_events`
（append-only）、寫 `employee_egg_ledger` 負向消耗 row、更新 pet 快取（health/vitality/streak/last_fed_at/balances）。
回傳 200 + `PetDashboardResponse`（更新後狀態）。router 不算數值，全由 service。

### POST /employee-pets/exchange

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must own a pet |
| `direction` | `Literal["white_to_silver","silver_to_gold"]` | yes | — | closed set |
| `count` | `int` | no | `1` | `>= 1`（要產出幾顆高階蛋）|

**Behaviour:** 呼叫 `employee_pet_service.exchange_eggs(session, employee_id, direction, count)`。
兌換率讀 `employee_reward_pool`（白→銀 `white_per_silver`、銀→金 `silver_per_gold`，預設 10/10）。
餘額不足 → service 拋 `ValidationError` → **422**。成功寫兩筆 ledger（`exchange.down` 消耗 + `exchange.up` 產出）、
更新快取餘額。回傳 200 + `ExchangeResponse`（消耗/產出顆數＋更新後四種餘額）。

### GET /employee-pets/tasks

| Query field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | — |
| `business_date` | `date \| None` | no | server「今日」(Asia/Taipei) | ISO date |

**Behaviour:** 呼叫 `employee_pet_service.list_today_tasks(session, employee_id, business_date)`，
回傳 `list[TaskWithStatusResponse]`：該日適用的任務定義（`is_active=True`、scope 到 store 或全租戶通用）
＋該員工當日完成狀態（`not_started` / `submitted` / `approved` / `rejected`，由 `employee_task_completions` 推導）。

### POST /employee-pets/tasks/{task_id}/complete

`task_id` (path): `UUID`，must exist in `employee_tasks` 且 `is_active`（否則 404）。

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must exist in `employees` |
| `business_date` | `date \| None` | no | server「今日」(Asia/Taipei) | ISO date |
| `evidence_url` | `str \| None` | no | `None` | <= 512 chars；若任務 `requires_evidence` 則必填（否則 422）|

**Behaviour:** 呼叫 `employee_pet_service.submit_task_completion(session, employee_id, task_id, business_date, evidence_url)`。
1. 若任務 `requires_approval=False` 且不需證明 → 直接 `status='approved'`、`egg_granted=True`、寫蛋帳本（`reason='task.complete'`）。
2. 若任務 `requires_approval=True` → 建 `status='submitted'` 的 completion、**不發蛋**（等 admin review）。
3. **冪等**：同 `(employee_id, task_id, business_date)` 且既有 completion `status<>'rejected'` → service 偵測既存、
   **回該既有 completion，200**，不重複建、不重複發蛋（被拒的不佔位、可重交）。
回傳 201（首次建立）/ 200（冪等重放）+ `TaskCompletionResponse`。

### POST /employee-pets/redemptions

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must own a pet |
| `store_id` | `UUID` | yes | — | must exist in `stores` |
| `redemption_type` | `RedemptionType` | yes | — | gold_egg / emperor / monthly_goal |
| `eggs_spent` | `int` | no | `0` | `>= 0`（gold_egg 兌現須 >0；service 校驗門檻）|

**Behaviour:** 呼叫 `employee_reward_service.request_redemption(session, employee_id, store_id, redemption_type, eggs_spent)`。
service 決定 `cash_amount`（讀 `employee_reward_pool`：`gold_egg_cash_value` / `emperor_bonus` / `monthly_goal_bonus`）、
扣蛋寫 `redeem.cash` ledger（蛋不足 → `ValidationError` → 422）、建 redemption。**月池閘（PRD D7）**：
- `monthly_pool_spent + cash_amount <= monthly_pool_budget` → `status='pending'`（待 admin 核可）。
- 超過上限 → `status='queued'`、`queued_for=次月 1 號`、**蛋不退**（資格保留），下月池重置後優先發放。
回傳 201 + `RedemptionResponse`（含 `status` 與 `queued_for`，讓前端能顯示「已排隊至次月」）。

### GET /employee-pets/leaderboard

| Query field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist in `stores` |
| `limit` | `int` | no | `20` | `1..100` |

**Behaviour:** 呼叫 `employee_pet_service.store_leaderboard(session, store_id, limit)`，**scope 到該 store**，
依（stage 高→低、level 高→低、feeding_streak_days 高→低）排序，回傳 `list[LeaderboardEntryResponse]`。
**不**跨店外洩（其他 store 的雞不得出現）。

### GET /employee-pets/admin/pending  (admin-only)

| Query field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist in `stores` |

**Behaviour:** 呼叫 `employee_pet_service.list_pending_completions(session, store_id)` ＋
`employee_reward_service.list_pending_redemptions(session, store_id)`，回傳 `AdminPendingResponse`
（`completions: list[TaskCompletionResponse]`（status=submitted）＋ `redemptions: list[RedemptionResponse]`
（status in pending/queued））。scope 到該 store。

### POST /employee-pets/admin/completions/{completion_id}/review  (admin-only)

`completion_id` (path): `UUID`，must exist（否則 404）。

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `reviewer_id` | `UUID` | yes | — | must exist in `employees`（店長/老闆）|
| `decision` | `Literal["approve","reject"]` | yes | — | closed set |
| `note` | `str \| None` | no | `None` | <= 512 chars |

**Behaviour:** 呼叫 `employee_pet_service.review_completion(session, completion_id, reviewer_id, decision, note)`。
- `approve`：`status='approved'`、`reviewed_by/reviewed_at` 填入；若 `egg_granted=False` 則經 service 發蛋
  （寫 `task.complete` ledger、設 `egg_granted=True`）。重複 approve 已核可者 → `ConflictError` → 409。
- `reject`：`status='rejected'`、**不發蛋**。
回傳 200 + `TaskCompletionResponse`。

### POST /employee-pets/admin/redemptions/{redemption_id}/review  (admin-only)

`redemption_id` (path): `UUID`，must exist（否則 404）。

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `reviewer_id` | `UUID` | yes | — | must exist in `employees` |
| `decision` | `Literal["approve","reject","mark_paid"]` | yes | — | closed set |
| `note` | `str \| None` | no | `None` | <= 512 chars |

**Behaviour:** 依 `decision` 直接分派到 reward service 對應函式（**無** `review_redemption` 包裝函式）：
- `approve` → `employee_reward_service.approve_redemption(session, ...)`：pending→approved、月池容量檢查、`monthly_pool_spent += cash_amount`（超頂 → `BudgetExceededError` → 409/422）。
- `reject` → `employee_reward_service.reject_redemption(session, ...)`：→rejected，**退蛋寫反向 `redeem.refund` ledger**（service 負責）。
- `mark_paid` → `employee_reward_service.mark_paid(session, ...)`：approved→paid、`paid_at` 填入。
狀態機非法轉換（如對已 paid 再 approve）→ `ConflictError` → 409。回傳 200 + `RedemptionResponse`。

---

## Pydantic Schemas

所有 input model：`ConfigDict(frozen=True, extra="forbid")`。**不要** `strict=True`
（pydantic v2 strict 會擋 JSON-string UUID — CLAUDE.md 明定的坑）。改用本檔的
`_reject_float` `BeforeValidator` 對任何數值欄位拒 `float`，並用 `_ensure_tz_aware` 拒 tz-naive datetime
（手法照 `restaurant_api/schemas/clock.py`）。Response model：`ConfigDict(from_attributes=True)`；
所有 timestamp 以 `Asia/Taipei` zone-aware ISO8601 字串輸出。`EggType` / `RedemptionType` 字面值域
必須與 `specs/employee_pet_models.md` 完全一致。

```python
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

EggType = Literal["white", "silver", "gold", "red"]
PetStage = Literal["chick", "hen", "big", "emperor"]
RedemptionType = Literal["gold_egg", "emperor", "monthly_goal"]
RedemptionStatus = Literal["pending", "approved", "paid", "rejected", "queued"]
CompletionStatusOut = Literal["not_started", "submitted", "approved", "rejected"]


def _reject_float(v: object) -> object:
    """Reject JSON ``float`` (e.g. 1.5) for count/amount fields — avoids the
    IEEE-754 → Decimal trap and keeps egg counts integral."""
    if isinstance(v, float):
        raise ValueError("must not be a float; send an integer or Decimal string")
    return v


def _ensure_tz_aware(v: datetime, name: str) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError(f"{name} must be timezone-aware")
    return v


Count = Annotated[int, BeforeValidator(_reject_float)]


# --- requests ---

class CreatePetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    employee_id: UUID
    store_id: UUID
    name: str = Field(min_length=1, max_length=50)


class FeedRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    employee_id: UUID
    mode: Literal["egg", "food"]
    egg_type: Literal["white", "silver", "gold"] | None = None  # red 不可餵
    amount: Count = Field(default=1, ge=1)


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    employee_id: UUID
    direction: Literal["white_to_silver", "silver_to_gold"]
    count: Count = Field(default=1, ge=1)


class TaskCompleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    employee_id: UUID
    business_date: date | None = None
    evidence_url: str | None = Field(default=None, max_length=512)


class RedemptionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    employee_id: UUID
    store_id: UUID
    redemption_type: RedemptionType
    eggs_spent: Count = Field(default=0, ge=0)


class CompletionReviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reviewer_id: UUID
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=512)


class RedemptionReviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reviewer_id: UUID
    decision: Literal["approve", "reject", "mark_paid"]
    note: str | None = Field(default=None, max_length=512)


# --- responses ---

class PetDashboardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    store_id: UUID
    name: str
    stage: PetStage
    level: int
    health: int
    vitality: int
    nest_level: int
    feeding_streak_days: int
    last_fed_at: datetime | None          # Asia/Taipei
    white_balance: int
    silver_balance: int
    gold_balance: int
    red_count: int
    feed_balance: int


class ExchangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    employee_id: UUID
    consumed_egg_type: EggType
    consumed_count: int
    produced_egg_type: EggType
    produced_count: int
    white_balance: int
    silver_balance: int
    gold_balance: int


class TaskWithStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: UUID
    title: str
    category: str
    egg_type: EggType
    egg_qty: int
    requires_evidence: bool
    requires_approval: bool
    completion_status: CompletionStatusOut


class TaskCompletionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    task_id: UUID
    business_date: date
    evidence_url: str | None
    status: Literal["submitted", "approved", "rejected"]
    egg_granted: bool
    reviewed_by: UUID | None
    reviewed_at: datetime | None          # Asia/Taipei


class RedemptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    store_id: UUID
    redemption_type: RedemptionType
    eggs_spent: int
    cash_amount: Decimal
    status: RedemptionStatus
    queued_for: date | None
    requested_at: datetime                 # Asia/Taipei
    reviewed_at: datetime | None           # Asia/Taipei
    paid_at: datetime | None               # Asia/Taipei


class LeaderboardEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rank: int
    employee_id: UUID
    pet_name: str
    stage: PetStage
    level: int
    feeding_streak_days: int


class AdminPendingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    completions: list[TaskCompletionResponse]
    redemptions: list[RedemptionResponse]
```

> `business_date` 若帶值不另做 tz 檢查（date 無時區）；任何傳入的 datetime 欄位（未來若加）走
> `_ensure_tz_aware`。本 router 的請求面目前無 datetime 輸入欄位 —— tz-naive 拒收的測試對象是
> service 回寫後 response 端的 Asia/Taipei 輸出（見 AC-13、AC-14）。

---

## Database writes

> router 自己不寫 DB；以下是它呼叫的 service 在同一 request 交易內所做的寫入（commit 在 `get_db` DI 層）。

| Action | Tables written (via service) | Notes |
|---|---|---|
| `POST /employee-pets` | `employee_pets` (1) | unique `(tenant_id, employee_id)` 防重 |
| `POST /feed` | `employee_pet_care_events` (1, append-only)、`employee_egg_ledger` (≥1 負向)、`employee_pets` (UPDATE 快取) | 餘額不足不寫 |
| `POST /exchange` | `employee_egg_ledger` (2: down+up)、`employee_pets` (UPDATE 快取) | 一交易 |
| `POST /tasks/{id}/complete` | `employee_task_completions` (1)、`employee_egg_ledger` (0或1) | 需審核者不發蛋；冪等重放不重寫 |
| `POST /redemptions` | `employee_reward_redemptions` (1)、`employee_egg_ledger` (1 `redeem.cash`) | 月池滿→status=queued |
| `admin/completions/{id}/review` (approve) | `employee_task_completions` (UPDATE)、`employee_egg_ledger` (1 if 未發) | reject 不發蛋 |
| `admin/redemptions/{id}/review` | `employee_reward_redemptions` (UPDATE)、`employee_egg_ledger` (1 `redeem.refund` if reject)、`employee_reward_pool` (UPDATE spent if approve) | 狀態機受控 |

所有發放/兌換由 service 走 `services/audit_service.audit()` 寫稽核（router 不直接寫）。
`employee_egg_ledger` / `employee_pet_care_events` 是 append-only：修正一律寫反向 row，**禁止** UPDATE/DELETE。

---

## Error responses

> 全部走 `restaurant_api/api/errors.py` 的 `DomainError` 系列（envelope
> `{"error": {"code","message","details"}}`），**不**用 raw `HTTPException`。

| Status | Trigger | code |
|---|---|---|
| 404 | `employee_id` 無雞 / `task_id` / `completion_id` / `redemption_id` 不存在 | `NOT_FOUND` |
| 409 | 重複建立雞（一員工一雞）/ 任務/兌換狀態機非法轉換（重複 approve、對 paid 再審）| `CONFLICT` |
| 422 | Pydantic 驗證失敗（float 拒收、`mode=egg` 缺 `egg_type`、`egg_type=red` 餵食、`name` 超長、`count`/`amount`<1、未知欄位）| FastAPI 預設 422 |
| 422 | 餘額不足（餵食/兌換/兌現蛋不夠）/ `requires_evidence` 任務未帶 `evidence_url` | `VALIDATION_ERROR` |
| 500 | DB integrity / 未預期 | generic |

任務完成冪等重放**不**算 conflict — service 回既有 completion + 200。
發起兌換超月池**不**算錯誤 — 回 201、`status='queued'` + `queued_for`。

---

## Acceptance Criteria

> 每一條對應一個 pytest test function；檔案 `tests/routers/test_employee_pets_router.py`，
> 命名 `test_employee_pets_ac_NN_*`。用 `tests/conftest.py` 的 async `client` fixture
> （`httpx.AsyncClient` + `ASGITransport`，**不要** sync `TestClient`），每測一個 SAVEPOINT、
> scope 到 `seed_tenant` / `seed_store` / `seed_employee` fixtures。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | 建立並命名雞 | `POST /employee-pets`（employee_id=E, store_id=S, name="阿雞"）→ 201；response `name="阿雞"`、`stage="chick"`、`level=1`、`health=100`、`vitality=100`、四種蛋餘額與 `feed_balance` 皆 0。 |
| AC-2 | 重複建立 409 | 同 employee 第二次 `POST /employee-pets` → 409、body `error.code="CONFLICT"`；DB 仍只有 1 隻雞。 |
| AC-3 | me 主畫面 | 建雞後 `GET /employee-pets/me?employee_id=E` → 200，回 `PetDashboardResponse`；未建雞的員工 `GET .../me` → 404 `NOT_FOUND`。 |
| AC-4 | 餵食改變狀態 | seed 一筆白蛋餘額>0 後 `POST /feed`（mode="egg", egg_type="white", amount=1）→ 200；response `white_balance` 比餵前少 1、`last_fed_at` 非 null。 |
| AC-5 | 兌換餘額不足 422 | 白蛋餘額=5 時 `POST /exchange`（direction="white_to_silver", count=1，需 10 顆）→ 422、`error.code="VALIDATION_ERROR"`；DB 無新 ledger row。 |
| AC-6 | 兌換成功扣換 | 白蛋餘額=10 時 `POST /exchange`（white_to_silver, count=1）→ 200；response `consumed_count=10`、`produced_count=1`、`silver_balance` +1、`white_balance` -10。 |
| AC-7 | 任務完成冪等 | 不需審核任務，同 `(employee_id, task_id, business_date)` 連送兩次 `POST /tasks/{id}/complete` → 第一次 201、第二次 200（回同一 completion id）；`employee_egg_ledger` 只多 1 筆 `task.complete`（蛋只發一次）。 |
| AC-8 | 需審核任務未核可不發蛋 | `requires_approval=True` 的任務 `POST /tasks/{id}/complete` → 201、`status="submitted"`、`egg_granted=False`；該員工 `employee_egg_ledger` 無新 `task.complete` row。 |
| AC-9 | requires_evidence 未帶證明 422 | `requires_evidence=True` 任務 complete 未帶 `evidence_url` → 422。 |
| AC-10 | 發起兌換超月池→queued | pool `monthly_pool_budget=6000`、`monthly_pool_spent=5000`，發起 `redemption_type="emperor"`（cash=5000）→ 201、`status="queued"`、`queued_for` 為次月 1 號；蛋未退（ledger 有 `redeem.cash`、無 `redeem.refund`）。 |
| AC-11 | admin 核可兌換 | pending redemption 經 `admin/redemptions/{id}/review`（decision="approve"）→ 200、`status="approved"`；`employee_reward_pool.monthly_pool_spent` 增加 `cash_amount`。 |
| AC-12 | 排行榜 scope 到本店 | store A 與 store B 各有雞，`GET /employee-pets/leaderboard?store_id=A` → 回傳只含 store A 的雞，無任何 store B 雞；`rank` 由 1 起遞增。 |
| AC-13 | float 被拒 | `POST /feed` body `amount=1.5`（JSON float）→ 422；`amount="1"`（字串整數）可通過。 |
| AC-14 | Asia/Taipei 輸出 | 建雞並餵食後，response `last_fed_at` 字串後綴 `+08:00`（DB 存 UTC、API 回台北時區）；`redemptions` 的 `requested_at` 同樣 `+08:00`。 |
| AC-15 | red 蛋不可餵 | `POST /feed`（mode="egg", egg_type="red"）→ 422（schema 值域不含 red）。 |
| AC-16 | admin reject 退蛋 | 對一筆已扣蛋的 pending redemption `review`（decision="reject")→ 200、`status="rejected"`；`employee_egg_ledger` 新增一筆 `redeem.refund`（正向）。 |
| AC-17 | admin pending scope | `GET /employee-pets/admin/pending?store_id=A` → 回 store A 的 submitted completions ＋ pending/queued redemptions，不含 store B 的。 |
| AC-18 | 狀態機衝突 409 | 對已 `approved` 的 completion 再次 `review`（approve）→ 409 `CONFLICT`。 |

---

## Tests

- 檔案：`tests/routers/test_employee_pets_router.py`
- 框架：`pytest` + `pytest-asyncio`，用 `conftest.py` 的 `client`（`httpx.AsyncClient` + `ASGITransport`）。
  **禁止** sync `TestClient`（會 "Future attached to a different loop"）。
- DB：每 test 一個 SAVEPOINT（`db_session` fixture）、teardown rollback、DB 永遠乾淨。
- 查詢務必 scope 到 fixture 的 `seed_tenant` / `seed_store` / `seed_employee`，**不要**全表掃描（會撞 seed/demo 資料）。
- service 依賴（`employee_pet_service` / `employee_reward_service`）在本 router 測試走**真實實作**
  （integration），用 seed fixtures 鋪前置餘額/任務/pool；若 service spec 尚未實作，可先 stub 對應 service
  函式，待 service 落地後改回真實。
- Coverage 目標：每條 AC + 每個錯誤碼路徑（404 / 409 / 422 各至少 1）至少 1 test。

---

## Out of scope

- **Authentication / authorization**：Phase 2。pilot 假設所有 endpoints 已通過上游 gateway；
  `employee_id` / `store_id` / `reviewer_id` 由 body/query 帶入。標 **admin** 的 3 條路由的真正
  authz 邊界由 Phase 2 gateway 強制（比照 `orders_router` 的 authn out-of-scope 寫法），本 spec 僅標註。
- **同儕互送蛋**：Phase 2（PRD D8）—— 本 router **不得**出現任何 peer-transfer / gift 路由。
- **顧客雙邊飛輪**：Phase 2（PRD §6）。
- **Multi-currency**：TWD only；`cash_amount` 一律 `Decimal` TWD。
- **每日結算 job**（考勤自動發蛋 / 衰退）：`employee_egg_settlement_job` spec，本 router 不含。
- **業務邏輯實作**（餘額重算、升階運算、月池排隊掃描）：在 `employee_pet_service` /
  `employee_reward_service` spec；本 router 只委派、不計算。
- **任務定義 CRUD**（建立/編輯 `employee_tasks`）：另開 admin 設定 spec；本 router 只讀任務、收完成。
- **LINE 推播提醒**（餵雞提醒）：Phase B；走 `integrations/line`，不在本 router。
- **分頁/篩選**：leaderboard 只取 `limit` 名；pending 不分頁（pilot 單店量小），需要時再加。
- **WebSocket / SSE 即時更新**：Phase 2+。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `employee_pet_service`（`specs/employee_pet_service.md`，已定稿） | router 呼叫 `get_pet_dashboard` / `create_pet` / `feed_pet` / `exchange_eggs` / `list_today_tasks` / `submit_task_completion` / `store_leaderboard` / `list_pending_completions` / `review_completion`（皆已定義於 service spec） |
| `employee_reward_service`（`specs/employee_reward_service.md`，已定稿） | router 呼叫 `request_redemption` / `list_pending_redemptions`；admin review 依 `decision` 分派 `approve_redemption` / `reject_redemption` / `mark_paid`（**無** `review_redemption` 包裝） |
| `models/gamification.py`（`specs/employee_pet_models.md`）| 7 表 + enum 值域的 SSOT；本 spec 的 `EggType` / `RedemptionType` 等字面值域必須一致 |
| `restaurant_api/api/deps.py` | `DbSession`（= `Depends(get_db)`）；commit 在 DI 層 |
| `restaurant_api/api/errors.py` | `NotFoundError` / `ConflictError` / `ValidationError`（service 拋、handler 轉 JSON 信封）|
| `restaurant_api/schemas/clock.py` | 借用 `_ensure_tz_aware` / float-reject `BeforeValidator` 慣例 |
| `services/audit_service.audit()` | 所有發放/兌換的稽核（由 service 寫，不在 router）|
| `restaurant_api/main.py` | router 透過 `app.include_router(employee_pets.router)` 掛載（main 的 wiring 另案落地，比照 `clock.py` 註記；測試以 autouse fixture 或既有 app mount 驗）|

---

## 給 PM Agent / Architect 的提醒

- **一員工一雞**靠 DB unique `(tenant_id, employee_id)`（models spec 已建）；router 的 409 應由 service
  捕捉 IntegrityError 轉 `ConflictError`，不要在 router 先 SELECT-then-INSERT（競態）。
- **冪等任務完成**靠 models 的 partial unique `(employee_id, task_id, business_date) WHERE status<>'rejected'`；
  router 對「冪等重放回 200」與「真衝突回 409」的差異要靠 service 區分（重放 = 同人同任務同日已存在且非 rejected）。
- **月池排隊（D7）不是錯誤**：超池回 201 + `queued`，前端要能顯示「已排隊至次月」，別讓員工以為失敗。
- **紅蛋陷阱**：`EggType` 含 `red`，但 feed/exchange 的請求面值域**刻意排除 red**（red 只能由結算 job penalty 寫入、
  只扣遊戲健康，呼應 D1 合規）。Coder 不要把 red 加進 `FeedRequest.egg_type`。
- **金錢欄位**只有 `cash_amount`（Decimal TWD）；蛋與飼料是**整數**，不要用 Decimal/float。
- **不要在 router 寫業務邏輯** —— 比照 `clock.py`，每個 handler 就一行 `return await service.fn(...)`。

— end of spec —
