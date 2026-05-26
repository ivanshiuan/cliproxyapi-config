# Spec: Cost Events Router (`/events`)

> **Module name:** `restaurant_api.routers.cost_events`
> **Owner domain:** Hidden-cost capture (報廢 / 員工餐 / 試吃)
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched:** `restaurant_api/models/cost_events.py`
> (WasteEvent, StaffMealEvent, TastingEvent),
> `restaurant_api/models/inventory.py` (StockMovement, MovementType, Ingredient, Recipe),
> `restaurant_api/models/menu.py` (MenuItem),
> `restaurant_api/models/employees.py` (Employee)

---

## Background

「真實 P&L 系統」最關鍵的差異化能力，就是把**隱性成本**（hidden cost）每一筆顯性化。本 router 處理三類事件：

| 類型 | 中文 | 觸發情境 |
|---|---|---|
| `waste` | 報廢 | 食材壞了 / 掉地 / 做壞 / 過效期 |
| `staff_meal` | 員工餐 | 員工吃了一份貨；可能是 menu 上的品項，也可能是廚房隨手煮 |
| `tasting` | 試吃 / 試菜 | R&D 試做、VIP 試菜、媒體拍照、訓練 portion |

每一筆事件都對應：

1. **一筆「typed row」** in `waste_events` / `staff_meal_events` / `tasting_events`（供 P&L view 分欄聚合）。
2. **一筆 `stock_movements`** (negative qty) — 把對應的 ingredient 從 ledger 扣掉，並在 `source_table` / `source_id` 指回 typed event row。

**三類事件都是 immutable 的**（一旦寫入不可修改、不可刪除）— 如果發生資料錯誤，新增一筆反向 event 來修正（後續另開 spec）。

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/events/waste` | 記錄一筆報廢事件 + 對應 stock_movement |
| `POST` | `/events/staff-meal` | 記錄一筆員工餐 + 對應 stock_movement(s) |
| `POST` | `/events/tasting` | 記錄一筆試吃 + 對應 stock_movement |
| `GET`  | `/events` | 列出三類事件，filter by date range / type / store |

OpenAPI tag：`events`。
所有路由注入 `session: AsyncSession = Depends(get_session)`。

### POST /events/waste

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist |
| `ingredient_id` | `UUID` | yes | — | must exist; `is_active=True` |
| `qty` | `Decimal` | yes | — | `> 0`, 4dp（語意是「報廢的數量」，正值；router 落 ledger 時自動加負號） |
| `cost` | `Decimal` | yes | — | `>= 0`，TWD（食材成本 = qty × unit_cost；由呼叫端用 weighted-avg 算或塞 0） |
| `reason` | `str` | yes | — | 1..500 chars；e.g. `'expired'`, `'spoiled'`, `'cooking_error'`, `'dropped'`, `'other'` |
| `reported_by` | `UUID \| None` | no | `None` | must exist in `employees` if present |
| `occurred_at` | `datetime` | no | server `now()` | tz-aware UTC |
| `external_ref` | `str \| None` | no | `None` | idempotency key |

**Behaviour:**

1. INSERT `waste_events` row → get `event.id`。
2. INSERT `stock_movements` row：`movement_type='waste'`、`qty = -request.qty`（負）、`ingredient_id`、`store_id`、`occurred_at`、`source_table='waste_events'`、`source_id=event.id`。
3. 同一 txn；失敗 rollback。
4. Response 201 + `WasteEventResponse`。

### POST /events/staff-meal

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist |
| `employee_id` | `UUID` | yes | — | must exist |
| `menu_item_id` | `UUID \| None` | no | `None` | must exist if present |
| `ingredients_used` | `dict[UUID, Decimal] \| None` | no | `None` | JSONB; key=ingredient_id, value=qty>0 |
| `total_cost` | `Decimal` | yes | — | `>= 0` TWD |
| `occurred_at` | `datetime` | no | server `now()` | tz-aware UTC |
| `external_ref` | `str \| None` | no | `None` | idempotency key |

**Cross-field validation:**

- Exactly one of (`menu_item_id`, `ingredients_used`) **must** be set；兩者都填或都不填 → 422 `staff_meal_source_ambiguous`。
- 若 `ingredients_used` 帶值：每個 value 必須 `> 0`，否則 422。

**Behaviour:**

1. INSERT `staff_meal_events` row → `event.id`。
2. **若 `menu_item_id` 指定**：透過 BOM (current `recipes`) 展開為 `[(ingredient_id, qty_per_serving)]`。對每筆寫 1 筆 `stock_movements`，`movement_type='staff_meal'`、`qty = -qty_per_serving`、`source_table='staff_meal_events'`、`source_id=event.id`。若 menu_item 無對應 recipe → 422 `no_recipe`（不允許靜默落地零筆 movement）。
3. **若 `ingredients_used` 指定**：對 dict 中每對 (ingredient_id, qty) 寫 1 筆 `stock_movements`（同樣 negative qty, source=staff_meal_events）。
4. 同一 txn。
5. Response 201 + `StaffMealEventResponse`，含 `movements_created: int` 計數。

### POST /events/tasting

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist |
| `menu_item_id` | `UUID \| None` | no | `None` | must exist if present |
| `ingredient_id` | `UUID \| None` | no | `None` | must exist if present |
| `qty` | `Decimal` | yes | — | `> 0`, 4dp |
| `total_cost` | `Decimal` | yes | — | `>= 0` TWD |
| `purpose` | `str` | yes | — | 1..200 chars；常見值：`'rnd'`, `'vip'`, `'media'`, `'training'` |
| `occurred_at` | `datetime` | no | server `now()` | tz-aware UTC |
| `external_ref` | `str \| None` | no | `None` | idempotency key |

**Cross-field validation:**

- Exactly one of (`menu_item_id`, `ingredient_id`) must be set；否則 422 `tasting_source_ambiguous`。

**Behaviour:**

1. INSERT `tasting_events`。
2. 若 `menu_item_id` → BOM 展開為 N 筆 movements（同 staff_meal 邏輯，`movement_type='tasting'`）。
3. 若 `ingredient_id` → 1 筆 movement，`qty = -request.qty`。
4. 同一 txn。
5. Response 201 + `TastingEventResponse`。

### GET /events

Query params:

| Param | Type | Default | Validation |
|---|---|---|---|
| `store_id` | `UUID \| None` | `None` | optional filter |
| `event_type` | `Literal["waste","staff_meal","tasting"] \| None` | `None` | 若 None → 三類 union |
| `from_date` | `date \| None` | `None` | TPE day, inclusive |
| `to_date` | `date \| None` | `None` | TPE day, inclusive；若 `< from_date` → 422 |
| `limit` | `int` | `200` | 1..500 hard cap |

回傳 `list[EventListItem]`（discriminated union over the three response shapes），`occurred_at DESC` 排序。

---

## Pydantic Schemas

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- requests ---

class WasteEventCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    ingredient_id: UUID
    qty: Decimal = Field(gt=Decimal("0"))
    cost: Decimal = Field(ge=Decimal("0"))
    reason: str = Field(min_length=1, max_length=500)
    reported_by: UUID | None = None
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

class StaffMealEventCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    employee_id: UUID
    menu_item_id: UUID | None = None
    ingredients_used: dict[UUID, Decimal] | None = None
    total_cost: Decimal = Field(ge=Decimal("0"))
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def check_exactly_one_source(self) -> "StaffMealEventCreate":
        a = self.menu_item_id is not None
        b = self.ingredients_used is not None and len(self.ingredients_used) > 0
        if a == b:
            raise ValueError("exactly one of menu_item_id, ingredients_used must be set")
        if b:
            for k, v in self.ingredients_used.items():
                if v <= Decimal("0"):
                    raise ValueError(f"ingredients_used[{k}] must be > 0")
        return self

class TastingEventCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    menu_item_id: UUID | None = None
    ingredient_id: UUID | None = None
    qty: Decimal = Field(gt=Decimal("0"))
    total_cost: Decimal = Field(ge=Decimal("0"))
    purpose: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def check_exactly_one_source(self) -> "TastingEventCreate":
        if (self.menu_item_id is None) == (self.ingredient_id is None):
            raise ValueError("exactly one of menu_item_id, ingredient_id must be set")
        return self

# --- responses ---

class WasteEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_type: Literal["waste"] = "waste"
    store_id: UUID
    ingredient_id: UUID
    qty: Decimal
    cost: Decimal
    reason: str
    reported_by: UUID | None
    occurred_at: datetime  # Asia/Taipei
    movement_id: UUID

class StaffMealEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_type: Literal["staff_meal"] = "staff_meal"
    store_id: UUID
    employee_id: UUID
    menu_item_id: UUID | None
    ingredients_used: dict[UUID, Decimal] | None
    total_cost: Decimal
    occurred_at: datetime  # Asia/Taipei
    movements_created: int

class TastingEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_type: Literal["tasting"] = "tasting"
    store_id: UUID
    menu_item_id: UUID | None
    ingredient_id: UUID | None
    qty: Decimal
    total_cost: Decimal
    purpose: str
    occurred_at: datetime  # Asia/Taipei
    movements_created: int

# EventListItem = discriminated union by event_type for GET /events
```

---

## Database writes

| Endpoint | Tables | Notes |
|---|---|---|
| `POST /events/waste` | `waste_events` (1), `stock_movements` (1, type=waste, qty<0) | one txn |
| `POST /events/staff-meal` (menu_item path) | `staff_meal_events` (1), `stock_movements` (N, type=staff_meal, qty<0) | N = #ingredients in recipe |
| `POST /events/staff-meal` (ingredients_used path) | `staff_meal_events` (1), `stock_movements` (N, type=staff_meal, qty<0) | N = len(ingredients_used) |
| `POST /events/tasting` | `tasting_events` (1), `stock_movements` (1 or N) | depending on source |
| `GET /events` | read-only | union over 3 tables |

**Immutability**：

- 三類 event 表本 spec **不**提供 PATCH / PUT / DELETE endpoint。
- DB schema 未在 event 表加 RULE block，但 router 層自我約束：source 不得出現 `update(WasteEvent)` 或類似呼叫（grep 驗證納入 test）。
- `stock_movements` 本身已是 append-only。

**Idempotency**：

- 三類 endpoint 都支援 `external_ref`：若 `(tenant_id, store_id, event_table, external_ref)` 已存在 → 200 + existing response，**不**重複落 ledger。
- 無 `external_ref` → 沒有自動去重（同 reason + 同時間的兩筆視為兩次合法事件）。

---

## Error responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed body | `{"detail":...}` |
| 404 | `ingredient_id` / `menu_item_id` / `employee_id` / `store_id` 不存在 | `{"detail":"<resource> not found"}` |
| 422 | Pydantic validation（float、negative qty、tz-naive、cross-field 衝突） | FastAPI default |
| 422 | `staff_meal_source_ambiguous` | `{"detail":"exactly one of menu_item_id, ingredients_used","code":"staff_meal_source_ambiguous"}` |
| 422 | `tasting_source_ambiguous` | 同上 |
| 422 | `no_recipe`（menu_item 路徑但 BOM 為空） | `{"detail":"menu item has no recipe","code":"no_recipe"}` |
| 422 | `to_date < from_date` | FastAPI default |
| 500 | DB IntegrityError 未包裝 | generic |

不會回 409；event 是 immutable + append-only，不存在「已關閉」狀態衝突。idempotency 不視為 conflict。

---

## Acceptance Criteria

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Waste writes typed row + movement | `POST /events/waste` qty=`Decimal("0.5")` → 201；`waste_events` +1、`stock_movements` +1（type=waste、qty=`Decimal("-0.5")`、source_id 對齊 event.id）。 |
| AC-2 | Waste cost preserved | `cost=Decimal("87.50")` → DB 4dp 保留。 |
| AC-3 | Staff meal via menu_item triggers BOM | menu_item 有 3 ingredients → `staff_meal_events`+1、`stock_movements`+3、每筆 `movement_type='staff_meal'`、`qty<0`、`source_table='staff_meal_events'`。 |
| AC-4 | Staff meal via ingredients_used | `ingredients_used={X:1, Y:2}` → 2 筆 movements；qty 分別 -1、-2。 |
| AC-5 | Staff meal both set rejected | `menu_item_id` 與 `ingredients_used` 都帶 → 422 `staff_meal_source_ambiguous`。 |
| AC-6 | Staff meal neither set rejected | 兩者都 None → 422 `staff_meal_source_ambiguous`。 |
| AC-7 | Tasting via menu_item | 1 筆 tasting_events + N 筆 movements（type=tasting，N=BOM 行數）。 |
| AC-8 | Tasting via ingredient_id | 1 筆 tasting_events + 1 筆 movement，`qty = -request.qty`。 |
| AC-9 | no_recipe blocks staff_meal/tasting | menu_item 無對應 recipe → 422 `no_recipe`，DB 完全沒寫入（txn rollback）。 |
| AC-10 | GET /events filters by type | seed 各 1 筆 waste/staff_meal/tasting → `?event_type=waste` 只回 1 筆。 |
| AC-11 | GET /events filters by date range (TPE) | 一筆 occurred_at TPE 2025-05-01 23:59、一筆 2025-05-02 00:01 → `?from_date=2025-05-02&to_date=2025-05-02` 只回後者。 |
| AC-12 | Immutability (no PATCH endpoint) | OpenAPI schema 不含任何 PATCH/PUT/DELETE on `/events/*`。 |
| AC-13 | Idempotency by external_ref | 同 `external_ref` 第二次 POST → 200，DB 無新 row（event 表 + movements 表）。 |
| AC-14 | Float rejected | `qty=0.5` (float) → 422。 |
| AC-15 | Negative qty rejected | `qty=Decimal("-1")` → 422。 |
| AC-16 | tz-naive datetime rejected | `occurred_at='2025-05-01T10:00:00'` → 422。 |
| AC-17 | Asia/Taipei in response | DB UTC，response `occurred_at` 含 `+08:00`。 |
| AC-18 | One txn — atomic | staff_meal with 3 BOM lines, force movement 2 to fail（mock IntegrityError）→ events 表 + 已寫的 movements **完全 rollback**。 |

---

## Tests

- 檔案：`tests/routers/test_cost_events_router.py`
- 框架：`pytest` + `pytest-asyncio` + `httpx.AsyncClient`
- Fixtures：`seeded_ingredient`、`seeded_menu_item_with_recipe`（recipe 3 行）、`seeded_menu_item_no_recipe`、`seeded_employee`、`seeded_store`。
- Mock BOM expansion via the calc-engine `bom_consumer` for menu_item paths（unit test 階段 stub）；integration test 階段裝回。
- Immutability test：grep router source 確認沒有 `UPDATE waste_events` / `UPDATE staff_meal_events` / `UPDATE tasting_events` 或對應 SQLAlchemy `update()` / `delete()`.
- Atomic txn test：使用 SQLAlchemy event listener 在第 N 筆 movement INSERT 前 raise，斷言整個 txn rollback。

---

## Out of scope

- **Authentication / authorization**：Phase 2。
- **Multi-tenant**：Phase 2；MVP 從 store 反查 tenant。
- **Pagination**：MVP `limit` + 500 hard cap；cursor 分頁延後。
- **Websocket / SSE updates**：Phase 2+。
- **Edit / delete events**：本 spec 不含，三類 event 表 immutable；錯誤資料用「補記反向 event」處理（另開 spec）。
- **Auto cost calculation**：本 router 假設 `cost` / `total_cost` 由呼叫端算好（用 weighted-avg unit cost）；`cogs_variance_detector` 模組或下游可以 cross-check 但本 spec 不擋。
- **Waste reason enum**：`reason` 暫時 free text；MVP 不限定值，UI 端用建議清單。
- **批次 import (CSV)**：另開 spec。
- **照片附件**：另開 spec（每類 event 都會接 S3，目前 schema 沒欄位）。
- **跨日 / 跨店事件分配**：每筆事件強制單一 `store_id`、`occurred_at` 單時點。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `bom_consumer` (calc-engine spec) | router 在 menu_item-based staff_meal / tasting 呼叫，展開 BOM |
| `cogs_variance_detector` (calc-engine spec) | 下游 reader：把 typed event 累計 + 與理論成本比對 |
| `orders_router` | 共用 `stock_movements` ledger 與寫入慣例（append-only、signed qty、source_table+source_id）|
| `stock_intake_router` | 同上 |
| `mv_daily_pnl` (DB view `docs/04 §7`) | 下游消費者：`waste_cost`、`staff_meal_cost`、`tasting_cost` 三欄都從 typed event 表聚合 |
| `restaurant_api/main.py` | `app.include_router(cost_events.router)` 掛載 |
| `restaurant_api/models/cost_events.py` | source of truth for `WasteEvent` / `StaffMealEvent` / `TastingEvent` |
| `restaurant_api/models/inventory.py` | `StockMovement`, `MovementType` enum (`WASTE`, `STAFF_MEAL`, `TASTING`), `Recipe` for BOM expansion |
| `restaurant_api/models/menu.py` | `MenuItem` lookup |
| `restaurant_api/models/employees.py` | `Employee` lookup for `employee_id` / `reported_by` |

— end of spec —
