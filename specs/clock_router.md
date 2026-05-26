# Spec: Clock / 打卡 Router (`/clock`)

> **Module name:** `restaurant_api.routers.clock`
> **Owner domain:** HR / 人事
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched:** `restaurant_api/models/hr.py`
> (TimeClock, LeaveRequest, LeaveType, LeaveStatus),
> `restaurant_api/models/employees.py` (Employee)

---

## Background

人事成本是真實 P&L 的第三大支出（在 COGS 與固定成本之後），且 **勞基法 (LSA)** 對加班分級有嚴格要求：

| 工時類別 | 加成 | 來源欄位 |
|---|---|---|
| 正常工時 | 1.00× | `time_clocks.regular_hours` |
| 加班前 2h | 1.34× | `overtime_tier1_hours` |
| 加班後 2h | 1.67× | `overtime_tier2_hours` |
| 假日加班 | 2.00× | `holiday_hours` |

模型 `TimeClock` 已預先 bucket 好這四個欄位（`docs/04 §5`），目的是讓 `mv_daily_pnl` 一刀切完 join，不必在 view 裡重算分級。本 router 負責：

- 即時打卡（in / out）
- 請假申請（leave request）
- 「今天還在班上有誰」query

具體計算（如何把一個 shift 的 hours 拆進四個 bucket）由 `labor_hours_classifier` (calc-engine spec) 純函式完成；本 router 只負責呼叫它並把結果寫入。

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/clock/in` | 員工打卡上班 |
| `POST` | `/clock/out` | 員工打卡下班，計算四個工時 bucket |
| `POST` | `/clock/leave-request` | 員工提出請假申請 |
| `GET`  | `/clock/today` | 列出目前所有「上班中」員工（clock_out IS NULL） |

OpenAPI tag：`clock`。
所有路由注入 `session: AsyncSession = Depends(get_session)`。

### POST /clock/in

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must exist; `is_active=True`；`terminated_on` 為 null 或未來 |
| `store_id` | `UUID` | yes | — | must exist |
| `clock_in` | `datetime` | no | server `now()` | tz-aware UTC；不可在未來（容許 +60s clock skew） |
| `source` | `Literal["manual","qr","face","gps"]` | no | `"manual"` | closed set（記錄打卡方式，會 dump 到 `notes`，schema 沒獨立欄位） |
| `notes` | `str \| None` | no | `None` | <= 200 chars |

**Behaviour:**

1. 查詢「該 employee 是否已有 open clock」：`SELECT FROM time_clocks WHERE employee_id=:e AND clock_out IS NULL`。若有 → 409 `already_clocked_in`，回應內含現有的 `time_clock_id` 與 `clock_in_at`。
2. INSERT `time_clocks` row：`clock_in=clock_in`、`clock_out=NULL`、4 個 hours bucket 全為 `Decimal("0")`、`tenant_id` 由 employee 反查。
3. Response 201 + `ClockInResponse`（含 `time_clock_id`）。

### POST /clock/out

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must exist |
| `clock_out` | `datetime` | no | server `now()` | tz-aware UTC；必須 `> open clock_in` |
| `notes` | `str \| None` | no | `None` | <= 200 chars |

**Behaviour:**

1. 找 employee 唯一的 open clock：`SELECT FROM time_clocks WHERE employee_id=:e AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1`。沒有 → 409 `not_clocked_in`。若 `clock_out <= open.clock_in` → 422 `clock_order_violation`。
2. 呼叫 `labor_hours_classifier.classify(clock_in, clock_out, employee_role, is_holiday_for_date(clock_in))`，回傳 `(regular, ot1, ot2, holiday)` 四個 `Decimal`，總和 = 實際時長 hours。`is_holiday_for_date` MVP 暫定用 Asia/Taipei 的 `weekday() in (5,6)` 判斷週末為「例假日」(粗略；國定假日表將來另作)。
3. UPDATE `time_clocks` SET `clock_out`, `regular_hours`, `overtime_tier1_hours`, `overtime_tier2_hours`, `holiday_hours`。
4. Response 200 + `ClockOutResponse`，含 4 個 hours bucket 與總和。

**Note:** `TimeClock` 不在 append-only 約束內（與 `stock_movements` 不同）— 它是「以最終結果為準」的 record，允許 UPDATE 至 close。但**不允許 DELETE**（schema 沒 soft-delete 欄位，且財報相關）。

### POST /clock/leave-request

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `employee_id` | `UUID` | yes | — | must exist |
| `leave_type` | `LeaveType` enum | yes | — | one of `特休/事假/病假/婚假/喪假/產假/生理假/公假/其他` |
| `start_at` | `datetime` | yes | — | tz-aware UTC |
| `end_at` | `datetime` | yes | — | tz-aware UTC; `> start_at` |
| `hours` | `Decimal` | yes | — | `> 0`, 2dp；由呼叫端自行算（含「請假計算 8 小時上限」邏輯；本 router 不檢） |
| `reason` | `str \| None` | no | `None` | <= 500 chars |

**Behaviour:**

1. INSERT `leave_requests` with `status='pending'`、`approved_by=NULL`、`approved_at=NULL`。
2. Response 201 + `LeaveRequestResponse`。
3. 不做任何審批邏輯（approve / reject 走另一個 endpoint，本 spec 不含）。
4. **不**自動 check overlapping leave / clock conflict（留給 reviewer 的 audit job）。

### GET /clock/today

Query params:

| Param | Type | Default | Validation |
|---|---|---|---|
| `store_id` | `UUID \| None` | `None` | optional filter |

回傳 `list[OpenClockResponse]`：以 Asia/Taipei timezone 解 `today`，
回傳所有 `time_clocks WHERE clock_out IS NULL AND clock_in::date (TPE) = today_tpe`。

——「今天」由 server 解，Asia/Taipei；clock_in 在 UTC 23:00（隔天 TPE 07:00）也算「今天上班的人」，所以判斷必須先 `AT TIME ZONE 'Asia/Taipei'`。

每筆 response 含 `employee_id`, `employee_name`, `store_id`, `clock_in`(TPE), `elapsed_hours`(`now() - clock_in`, 2dp Decimal)。**不**做分頁；MVP 一間店現場員工數不會超過 30。

---

## Pydantic Schemas

```python
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- requests ---

class ClockInRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    employee_id: UUID
    store_id: UUID
    clock_in: datetime | None = None
    source: Literal["manual","qr","face","gps"] = "manual"
    notes: str | None = Field(default=None, max_length=200)

class ClockOutRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    employee_id: UUID
    clock_out: datetime | None = None
    notes: str | None = Field(default=None, max_length=200)

class LeaveRequestCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    employee_id: UUID
    leave_type: Literal["特休","事假","病假","婚假","喪假","產假","生理假","公假","其他"]
    start_at: datetime
    end_at: datetime
    hours: Decimal = Field(gt=Decimal("0"))
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_window(self) -> "LeaveRequestCreate":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be > start_at")
        return self

# --- responses ---

class ClockInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    time_clock_id: UUID
    employee_id: UUID
    store_id: UUID
    clock_in: datetime  # Asia/Taipei

class ClockOutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    time_clock_id: UUID
    employee_id: UUID
    clock_in: datetime  # Asia/Taipei
    clock_out: datetime  # Asia/Taipei
    regular_hours: Decimal
    overtime_tier1_hours: Decimal
    overtime_tier2_hours: Decimal
    holiday_hours: Decimal
    total_hours: Decimal  # sum of the four buckets, 2dp

class LeaveRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    leave_type: str
    start_at: datetime  # Asia/Taipei
    end_at: datetime    # Asia/Taipei
    hours: Decimal
    status: Literal["pending","approved","rejected","cancelled"]
    reason: str | None

class OpenClockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    time_clock_id: UUID
    employee_id: UUID
    employee_name: str
    store_id: UUID
    clock_in: datetime  # Asia/Taipei
    elapsed_hours: Decimal  # 2dp
```

---

## Database writes

| Action | Tables | Notes |
|---|---|---|
| `POST /clock/in` | `time_clocks` (1 INSERT) | hours buckets 全為 0 |
| `POST /clock/out` | `time_clocks` (1 UPDATE on the existing open row) | 由 calc-engine 填四個 bucket |
| `POST /clock/leave-request` | `leave_requests` (1 INSERT) | status=pending |
| `GET /clock/today` | none | read-only |

`time_clocks` 在 close 時的 UPDATE 是**唯一**的 UPDATE 路徑；任何「修正歷史紀錄」走 admin workflow（不在本 spec）。

---

## Error responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed body | `{"detail":...}` |
| 404 | `employee_id` 不存在 / 已 terminated | `{"detail":"employee not found"}` |
| 404 | `store_id` 不存在 | `{"detail":"store not found"}` |
| 409 | already clocked in | `{"detail":"employee already clocked in","code":"already_clocked_in","time_clock_id":"..."}` |
| 409 | not clocked in (on /out) | `{"detail":"employee is not clocked in","code":"not_clocked_in"}` |
| 422 | tz-naive datetime | FastAPI default |
| 422 | `clock_out <= clock_in` | `{"detail":"clock_out must be after clock_in","code":"clock_order_violation"}` |
| 422 | `clock_in` in the future > 60s | `{"detail":"clock_in cannot be in the future","code":"clock_future"}` |
| 422 | `hours <= 0` on leave request | FastAPI default |
| 422 | `end_at <= start_at` on leave request | FastAPI default (model_validator) |
| 500 | unexpected | generic |

---

## Acceptance Criteria

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Clock in writes row | `POST /clock/in` → 201、`time_clocks` 多 1 row、`clock_out IS NULL`、4 buckets = 0。 |
| AC-2 | Cannot clock in twice | 同 employee 連續 2 次 `POST /clock/in` → 第二次 409 `already_clocked_in`，回應含現有 `time_clock_id`。 |
| AC-3 | Clock out closes row | `POST /clock/out` → 200、原 row `clock_out` 不再 NULL、4 buckets 已 populated。 |
| AC-4 | Cannot clock out without clock in | 沒打卡上班直接 `POST /clock/out` → 409 `not_clocked_in`。 |
| AC-5 | Clock out before clock in rejected | `clock_out=clock_in - 1h` → 422 `clock_order_violation`。 |
| AC-6 | Total hours sums to 4 buckets | classifier 回傳 (regular=8, ot1=2, ot2=0, holiday=0) → response `total_hours=10`，DB 對應 row 4 個欄位也是。 |
| AC-7 | Holiday classification | clock_in 是週六 UTC（TPE 也是週六）→ classifier 把全部時數歸 `holiday_hours`，response `holiday_hours>0` 且 `regular_hours=0`。 |
| AC-8 | Leave request defaults to pending | `POST /clock/leave-request` → response `status='pending'`、DB row `approved_by=NULL`。 |
| AC-9 | Leave request end_at validation | `end_at == start_at` → 422。 |
| AC-10 | GET /clock/today filters by tz-day | seed 1 員工 clock_in at `2025-05-01T15:30Z`（TPE: 2025-05-01 23:30），於 TPE 2025-05-01 query → 該 row 出現；TPE 2025-05-02 query → 不出現（除非已跨日仍 open）。 |
| AC-11 | GET /clock/today excludes closed | 1 employee already clocked out today → 不在結果中。 |
| AC-12 | elapsed_hours updates | 兩次相隔 1 分鐘 query 同一 open clock → `elapsed_hours` 增加。 |
| AC-13 | Float rejected | `hours=8.0` (float) → 422。 |
| AC-14 | tz-naive datetime rejected | `clock_in='2025-05-01T10:00:00'` → 422。 |
| AC-15 | Asia/Taipei in response | DB UTC，response `clock_in` 含 `+08:00`。 |
| AC-16 | Terminated employee cannot clock in | seed employee with `terminated_on=yesterday` → 404 `employee not found`。 |

---

## Tests

- 檔案：`tests/routers/test_clock_router.py`
- 框架：`pytest` + `pytest-asyncio` + `httpx.AsyncClient`
- Fixtures：`seeded_employee`, `seeded_store`, freeze `datetime.now()` via `freezegun` 或注入 `clock_now` 依賴覆寫，方便驗證 `elapsed_hours`。
- `labor_hours_classifier` 在 unit test 階段 stub 回固定 `(8,2,0,0)`，holiday case stub 回 `(0,0,0,10)`；integration test 階段裝回真實實作。
- Holiday 邏輯由 stub 注入 `is_holiday_for_date` 函式，預設用 weekday=5,6。

---

## Out of scope

- **Authentication / authorization**：Phase 2（目前 endpoints 任何人可呼叫；上線前接 gateway）。
- **Multi-tenant**：Phase 2；MVP 由 employee 反查 `tenant_id`。
- **Pagination**：`/clock/today` 無分頁（單店人數小）；leave_requests 列表型查詢延後到另開 spec。
- **Websocket / SSE updates**（「員工 A 剛打卡」推播）：Phase 2+。
- **打卡地點驗證**（GPS fence、wifi MAC）：來源欄位 `source` 已保留，邏輯延後。
- **Leave approve / reject / cancel** workflow：另開 spec。
- **Shifts 排班** CRUD：另開 spec（`shifts` table 已存在，路徑保留 `/schedule`）。
- **國定假日表**：MVP 用 weekday 粗判週末；國定假日 (e.g. 春節、雙十) 之後接 `tw_holidays` 表。
- **Payroll period close / 薪資計算**：另一個模組讀 `time_clocks` 聚合，本 router 只寫 raw data。
- **打卡修正 / 改時間**：禁止 DELETE，UPDATE 僅限 close 路徑；歷史修正走 admin workflow。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `labor_hours_classifier` (calc-engine spec) | router 在 clock-out 時呼叫，回傳 4-tuple of `Decimal` |
| `is_holiday_for_date` (helper) | MVP 簡化版（weekday），未來換成查表；router 透過 DI |
| `mv_daily_pnl` (DB view) | 下游 reader：聚合 `time_clocks` × `employees.hourly_wage` × 加成係數 → `labor_cost` |
| `cost_events_router` | 不直接互通；但 `staff_meal_events` 會引用同樣的 `employee_id` |
| `orders_router` | 完全獨立；但同時也是 `tenant_id` 反查目標 |
| `restaurant_api/main.py` | `app.include_router(clock.router)` 掛載 |
| `restaurant_api/models/hr.py` | source of truth for `TimeClock` / `LeaveRequest` / `LeaveType` enum |
| `restaurant_api/models/employees.py` | resolve employee + tenant_id |

— end of spec —
