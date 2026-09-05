---
id: stock_intake_router
title: Spec: Stock Intake & Movements Router (`/stock`)
module: stock_intake_router
kind: router
status: implemented
preferred_model: opus
budget_usd: 5.0
tags: [inventory, router, audit]
ac_count: 16
---

# Spec: Stock Intake & Movements Router (`/stock`)

> **Module name:** `restaurant_api.routers.stock`
> **Owner domain:** ERP / Inventory
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched:** `restaurant_api/models/inventory.py`
> (Ingredient, StockMovement, MovementType),
> `restaurant_api/models/stores.py`

---

## Background

進銷存 ledger 是整個系統的「庫存真相」。所有食材進出都記錄為 `stock_movements`，一筆都不能少、一筆都不能改。本 router 提供 ledger 的**寫入入口**（進貨、盤點調整）與**讀取入口**（movements list、purchases list）。

`stock_movements` 是 **append-only**（`docs/04_data_schema.md §3` invariant 3，DB 層由 `INSTEAD NOTHING` rule 保證）：

- 進貨 → 一筆 `purchase` 正量 row。
- 盤點實盤 < 系統量 → `adjustment_out` 負量 row；實盤 > 系統量 → `adjustment_in` 正量 row。
- 修錯帳 = 再寫一筆反向，不允許 UPDATE/DELETE。

供應商 / 進貨單號 / 每行單價 都必須在 purchase 時捕捉，後續模組才能跑加權平均成本與配對發票。

> **註：** 目前 `restaurant_api/models/` 沒有獨立的 `purchase_orders` / `purchase_order_lines` ORM 模型，只在 `docs/04_data_schema.md §3` DDL 定義。本 spec 假設**最小可行**做法：在本 router 內以 `source_table='purchase_invoices'` + `source_id=external invoice uuid` 寫入 `stock_movements`，supplier/invoice 細節暫存於 `note` JSON-encoded payload，等將來 PO 模型落地後遷移。實作者可在落地 PO 模型同時做完整版。

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/stock/purchases` | 記錄一筆進貨（一張供應商發票，N lines）→ 寫 N 筆 `stock_movements` (`purchase`) |
| `GET`  | `/stock/purchases` | 列出進貨紀錄（從 movements 反推 by `source_table='purchase_invoices'`），支援 filter |
| `POST` | `/stock/adjustments` | 盤點調整（單一 ingredient 的差量）→ 寫 1 筆 `adjustment_in` 或 `adjustment_out` |
| `GET`  | `/stock/movements` | 通用 ledger 讀取，支援 filter by date / ingredient / type |

OpenAPI tag：`stock`。
所有路由注入 `session: AsyncSession = Depends(get_session)`。

### POST /stock/purchases

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist |
| `supplier_name` | `str` | yes | — | 1..100 chars |
| `supplier_tax_id` | `str \| None` | no | `None` | exactly 8 digits if present |
| `invoice_number` | `str` | yes | — | 1..20 chars（供應商 GUI 發票字軌或自填單號） |
| `invoice_date` | `date` | yes | — | not in the future |
| `received_at` | `datetime` | no | server `now()` | tz-aware UTC |
| `external_ref` | `str \| None` | no | `None` | idempotency key |
| `notes` | `str \| None` | no | `None` | <= 1000 chars |
| `lines` | `list[PurchaseLineCreate]` | yes | — | min length 1 |

**PurchaseLineCreate**

| Field | Type | Required | Validation |
|---|---|---|---|
| `ingredient_id` | `UUID` | yes | must exist in `ingredients` |
| `qty` | `Decimal` | yes | `> 0` (4dp) |
| `unit_cost` | `Decimal` | yes | `>= 0` (4dp); per-unit cost in TWD（unit aligns with ingredient.unit） |
| `expires_on` | `date \| None` | no | optional shelf-life marker; `>= invoice_date` |
| `note` | `str \| None` | no | <= 200 chars |

**Response (201 Created):** `PurchaseResponse`（見 schemas）。
**Behaviour:**

1. 計算 `total = sum(line.qty * line.unit_cost)`，以 `Decimal` 計算，不提前 round。
2. Generate one synthetic `purchase_invoice_id: UUID`（uuid7）作為這次進貨的 group key。
3. 對每一 line 寫入 1 筆 `stock_movements`：
   - `movement_type='purchase'`
   - `qty = +line.qty`
   - `ingredient_id = line.ingredient_id`
   - `store_id`、`tenant_id` from request
   - `occurred_at = received_at`
   - `source_table = 'purchase_invoices'`
   - `source_id = purchase_invoice_id`
   - `note = JSON({"supplier_name":..., "invoice_number":..., "unit_cost":..., "line_note":...})`（一行的 metadata 全部進 `note`；之後 PO model 落地時遷移為正規欄位）
4. 一切包在 same txn。

**Idempotency:**

- 若 request 含 `external_ref` 且已存在另一個進貨（identified by `note.external_ref`）→ 200 + existing PurchaseResponse。
- 若無 `external_ref` 但 `(tenant_id, supplier_tax_id, invoice_number)` 已落地過 → 409 `invoice_conflict`。

### GET /stock/purchases

Query params (all optional):

| Param | Type | Default | Validation |
|---|---|---|---|
| `store_id` | `UUID \| None` | `None` | filter by store |
| `from_date` | `date \| None` | `None` | inclusive |
| `to_date` | `date \| None` | `None` | inclusive；若 < from_date → 422 |
| `supplier_name` | `str \| None` | `None` | partial match (ILIKE) |

回傳 `list[PurchaseResponse]`（依 `received_at DESC` 排序，**MVP 不分頁**，hard cap 500 — 超過則 422 `too_many_results`，呼叫端收緊 date range）。

### POST /stock/adjustments

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist |
| `ingredient_id` | `UUID` | yes | — | must exist |
| `delta` | `Decimal` | yes | — | 非零；正數 = 補帳、負數 = 扣帳 |
| `reason` | `str` | yes | — | 1..200 chars; e.g. `'盤點'`、`'估算修正'` |
| `occurred_at` | `datetime` | no | server `now()` | tz-aware UTC |
| `external_ref` | `str \| None` | no | `None` | idempotency key |

**Behaviour:**

1. 由 `delta` 的正負號決定 `movement_type`：`delta > 0` → `adjustment_in`，`delta < 0` → `adjustment_out`。`delta == 0` → 422。
2. 寫入 1 筆 `stock_movements`：
   - `qty = delta`（保留正負號，符合 DB CHECK constraint）
   - `source_table = 'stock_adjustments'`
   - `source_id = uuid7()`（synthetic）
   - `note = reason` (+ external_ref if any)
3. **Idempotency:** 若 `external_ref` 已存在 → 200 + existing AdjustmentResponse。

### GET /stock/movements

Query params:

| Param | Type | Default | Validation |
|---|---|---|---|
| `store_id` | `UUID \| None` | `None` | |
| `ingredient_id` | `UUID \| None` | `None` | |
| `movement_type` | `MovementType \| None` | `None` | one of the enum values |
| `from_ts` | `datetime \| None` | `None` | tz-aware |
| `to_ts` | `datetime \| None` | `None` | tz-aware; if < from_ts → 422 |
| `limit` | `int` | `200` | 1..500 |

回傳 `list[StockMovementResponse]`（`occurred_at DESC`），respects `limit`. MVP 不做 cursor pagination。

---

## Pydantic Schemas

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

# --- requests ---

class PurchaseLineCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    ingredient_id: UUID
    qty: Decimal = Field(gt=Decimal("0"))
    unit_cost: Decimal = Field(ge=Decimal("0"))
    expires_on: date | None = None
    note: str | None = Field(default=None, max_length=200)

class PurchaseCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    supplier_name: str = Field(min_length=1, max_length=100)
    supplier_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    invoice_number: str = Field(min_length=1, max_length=20)
    invoice_date: date
    received_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)
    lines: list[PurchaseLineCreate] = Field(min_length=1)

class AdjustmentCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    ingredient_id: UUID
    delta: Decimal  # validated non-zero in @model_validator
    reason: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

# --- responses ---

class StockMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    store_id: UUID
    ingredient_id: UUID
    movement_type: Literal[
        "purchase","sale_consume","adjustment_in","adjustment_out",
        "waste","staff_meal","tasting","expiry","transfer_in","transfer_out",
    ]
    qty: Decimal
    source_table: str | None
    source_id: UUID | None
    occurred_at: datetime  # Asia/Taipei in response
    note: str | None

class PurchaseLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    ingredient_id: UUID
    qty: Decimal
    unit_cost: Decimal
    line_total: Decimal
    expires_on: date | None
    movement_id: UUID  # link back to the ledger row

class PurchaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    purchase_invoice_id: UUID
    store_id: UUID
    supplier_name: str
    supplier_tax_id: str | None
    invoice_number: str
    invoice_date: date
    received_at: datetime  # Asia/Taipei
    total: Decimal
    notes: str | None
    lines: list[PurchaseLineResponse]

class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    movement_id: UUID
    store_id: UUID
    ingredient_id: UUID
    delta: Decimal
    reason: str
    occurred_at: datetime  # Asia/Taipei
```

---

## Database writes

| Action | Tables | Notes |
|---|---|---|
| `POST /stock/purchases` | `stock_movements` (one INSERT per line) | grouped by synthetic `purchase_invoice_id`，stored in `source_id` |
| `POST /stock/purchases` (idempotent replay) | none | returns existing |
| `POST /stock/adjustments` | `stock_movements` (1 row) | `source_table='stock_adjustments'` |
| `GET /*` | read-only | |

**Sign convention enforced at DB by CHECK constraint** (`docs/04 §3`)：

- `purchase` / `adjustment_in` / `transfer_in` → qty > 0
- `sale_consume` / `waste` / `staff_meal` / `tasting` / `adjustment_out` / `transfer_out` → qty < 0

router 在落地前自行檢查，避免讓 DB CHECK 拋未經包裝的 IntegrityError。違反 → 422 with `code='sign_violation'`。

**Append-only invariant**：本 router **絕對不** 發 UPDATE / DELETE 對 `stock_movements`。所有 idempotency 路徑都靠「先查再決定要不要 INSERT」，不靠 upsert。

---

## Error responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed body | `{"detail":...}` |
| 404 | `ingredient_id` / `store_id` 不存在 | `{"detail":"ingredient not found"}` |
| 409 | invoice_conflict（同 supplier_tax_id + invoice_number 已落地） | `{"detail":"invoice already recorded","code":"invoice_conflict"}` |
| 422 | Pydantic validation（float、negative qty、tz-naive、delta=0、to_date < from_date） | FastAPI default |
| 422 | `sign_violation` | `{"detail":"qty sign does not match movement_type","code":"sign_violation"}` |
| 422 | `too_many_results` on GET | `{"detail":"narrow your date range","code":"too_many_results"}` |
| 500 | DB IntegrityError 未包裝 | generic |

---

## Acceptance Criteria

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Purchase 1 line writes 1 movement | `POST /stock/purchases` with 1 line → 201、`stock_movements` 多 1 row、`movement_type='purchase'`、`qty>0`。 |
| AC-2 | Purchase total computed | 2 lines: (qty=10, unit_cost=`Decimal("3.50")`) + (qty=5, unit_cost=`Decimal("12.0000")`) → response `total=Decimal("95.0000")`。 |
| AC-3 | Purchase idempotent by external_ref | 相同 `external_ref` 第二次 POST → 200，DB 無新 row。 |
| AC-4 | Invoice conflict | 不帶 `external_ref`，相同 `(supplier_tax_id, invoice_number)` 第二次 → 409 `invoice_conflict`。 |
| AC-5 | Adjustment positive → adjustment_in | `delta=Decimal("5")` → `movement_type='adjustment_in'`、`qty=+5`。 |
| AC-6 | Adjustment negative → adjustment_out | `delta=Decimal("-3")` → `movement_type='adjustment_out'`、`qty=-3`。 |
| AC-7 | Adjustment zero rejected | `delta=Decimal("0")` → 422。 |
| AC-8 | Append-only: no UPDATE path exists | router source 無 `update(StockMovement)` / `delete(StockMovement)` 呼叫（grep test）；ledger 行數只增不減在 round-trip test 驗證。 |
| AC-9 | Movements filter by ingredient | seed 3 ingredients × 2 movements each → `GET /stock/movements?ingredient_id=X` 只回那一個 ingredient 的 2 筆。 |
| AC-10 | Movements filter by type | seed 1 purchase + 1 waste → `GET ?movement_type=waste` 回 1 筆。 |
| AC-11 | Movements date range | `from_ts` / `to_ts` 過濾正確；`to_ts < from_ts` → 422。 |
| AC-12 | Decimal precision preserved | `unit_cost=Decimal("0.0125")` 落 DB 後 round-trip 仍 4dp（鹽巴等小單價）。 |
| AC-13 | Float rejected in lines | `unit_cost=3.50` (float) → 422。 |
| AC-14 | Asia/Taipei in responses | DB `occurred_at` UTC，response 字串含 `+08:00`。 |
| AC-15 | Sign violation guard | 直接呼叫內部 helper 寫 `purchase` + `qty=-1` → 422 `sign_violation`（不讓 IntegrityError 漏出）。 |
| AC-16 | tz-naive received_at rejected | `received_at='2025-05-01T10:00:00'` → 422。 |

---

## Tests

- 檔案：`tests/routers/test_stock_router.py`
- 框架：`pytest` + `pytest-asyncio` + `httpx.AsyncClient`
- DB：async fixture `seeded_ingredient` 提供已建立的 ingredient + store。
- 用 `seeded_movement_history` factory 預塞固定數量 movements，用 GET 端點驗證 filter。
- Append-only 驗證：在 round-trip test 中 snapshot `count(stock_movements)`，操作完再算一次，確認**只增不減**。

---

## Out of scope

- **Authentication / authorization**：Phase 2。
- **Multi-tenant**：Phase 2；MVP 由 store 反查 `tenant_id`。
- **Pagination**：defer until needed；MVP 用 `limit` + hard cap，無 cursor。
- **Websocket / SSE updates** on inventory changes：Phase 2+。
- **完整 PO / supplier master models**：本 spec 用 stop-gap（synthetic `purchase_invoice_id` + JSON note）。將來落地 `purchase_orders` ORM 後遷移。
- **加權平均成本計算**：本 router 只記錄 unit_cost；weighted-avg 在後續 `inventory_cost_calculator` 模組做。
- **FEFO（First-Expired-First-Out）扣料**：本 router 只記 `expires_on`，不做 picking logic。
- **負庫存警示**：write 端不擋；只允許 ledger 真實記錄，警示交給 P&L view。
- **跨店調撥** (`transfer_in` / `transfer_out`)：另開 spec，本次不含。
- **Bulk import (CSV)**：另開 spec。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `orders_router` | 共用 `stock_movements` 寫入慣例；orders 寫 `sale_consume`(-)，本 router 寫 `purchase`(+) / `adjustment_*` |
| `cost_events_router` | 共用 ledger；waste/staff_meal/tasting 都是負量 outbound |
| `bom_consumer` (calc-engine) | 不直接呼叫；但購進的食材最終透過 BOM 在 order 出單時被扣 |
| `cogs_variance_detector` (calc-engine) | 下游 reader：跑 weighted-avg + theoretical 比對，本 router 只負責塞 raw data |
| `uniform_invoice_validator` | **不**對供應商 invoice_number 強制驗證（供應商發票格式種類多樣，留 free text） |
| `mv_daily_pnl` (DB view) | 透過 `stock_movements` 聚合 COGS actual；本 router 是上游 |
| `restaurant_api/main.py` | `app.include_router(stock.router)` 掛載 |

— end of spec —
