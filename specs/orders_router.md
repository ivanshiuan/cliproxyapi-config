# Spec: Orders Router (`/orders`)

> **Module name:** `restaurant_api.routers.orders`
> **Owner domain:** Sales / POS-integration
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** FastAPI router mounted into `restaurant_api.main:app`
> **Models touched:** `restaurant_api/models/orders.py`,
> `restaurant_api/models/inventory.py` (StockMovement),
> `restaurant_api/models/menu.py` (MenuItem), `restaurant_api/models/employees.py`

---

## Background

訂單 (orders) 是整套真實 P&L 系統的**收入面入口**。POS+ / iCHEF / 手開單三條來源最終都必須在這支 API 落地成同一份 `orders` 紀錄，並透過 `order_lines` 觸發 BOM 自動扣料 (`bom_consumer`)、在 `stock_movements` 寫入 `sale_consume` 行；結帳時透過 `discount_resolver` 計算 `discount_total` 與 `net_revenue`；作廢時透過追加「反向 movement」維持 ledger 的 append-only 性質。

本 router 必須與 `docs/04_data_schema.md §4` 的 invariants 完全一致，並支援台灣特有的 **統一發票 / 載具 / 統編** 欄位，以及與外部 POS round-trip 用的 `external_pos_id` + `pos_source` 兩個欄位（idempotency key 之來源）。

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/orders` | 建立 open 狀態的新訂單（可含 lines / discounts / payments） |
| `GET`  | `/orders/{order_id}` | 讀取單筆訂單（含 lines / discounts / payments） |
| `POST` | `/orders/{order_id}/close` | 結帳：status → closed，計算 `net_revenue`，鎖定訂單 |
| `POST` | `/orders/{order_id}/void` | 作廢：status → voided，並對所有已產生的 stock_movements 寫入反向行（負量） |

所有路由 prefix：`/orders`；OpenAPI tag：`orders`。
所有路由都注入 `session: AsyncSession = Depends(get_session)`。

### POST /orders

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `store_id` | `UUID` | yes | — | must exist in `stores` |
| `order_no` | `str` | yes | — | 1..64 chars, 由呼叫端產生（POS 單號） |
| `business_date` | `date` | yes | — | ISO date, 不能晚於 UTC 今天 +1 日 |
| `opened_at` | `datetime` | no | server `now()` | tz-aware UTC; 若帶 naive 視為 422 |
| `external_pos_id` | `str \| None` | no | `None` | 1..64 chars; idempotency key when present |
| `pos_source` | `Literal["ichef","posplus","manual"]` | no | `"manual"` | closed set |
| `invoice_number` | `str \| None` | no | `None` | pattern `^[A-Z]{2}[0-9]{8}$`，若帶必須通過 `uniform_invoice_validator` |
| `carrier_type` | `Literal["mobile","citizen","member","paper"] \| None` | no | `None` | closed set |
| `carrier_id` | `str \| None` | no | `None` | 若 `carrier_type=="mobile"` 必須以 `/` 開頭並 8 chars |
| `buyer_tax_id` | `str \| None` | no | `None` | exactly 8 digits if present |
| `notes` | `str \| None` | no | `None` | <= 1000 chars |
| `lines` | `list[OrderLineCreate]` | no | `[]` | each line：見下表 |
| `discounts` | `list[OrderDiscountCreate]` | no | `[]` | 見下表 |
| `payments` | `list[OrderPaymentCreate]` | no | `[]` | 見下表 |

**OrderLineCreate**

| Field | Type | Required | Validation |
|---|---|---|---|
| `menu_item_id` | `UUID` | yes | must exist in `menu_items` |
| `qty` | `Decimal` | yes | `> 0`, 4dp |
| `unit_price` | `Decimal` | yes | `>= 0`, 4dp（snapshot — 即使 menu 改價，這裡留存售價） |
| `notes` | `str \| None` | no | <= 200 chars |

**OrderDiscountCreate**

| Field | Type | Required | Validation |
|---|---|---|---|
| `kind` | `Literal["percent","amount","comp","allowance","employee"]` | yes | closed set |
| `value` | `Decimal` | yes | if `kind=="percent"` then `0..1`，否則 `>= 0` TWD |
| `reason` | `str \| None` | no | <= 200 chars |
| `applied_by` | `UUID \| None` | no | must exist in `employees` if present |

**OrderPaymentCreate**

| Field | Type | Required | Validation |
|---|---|---|---|
| `method` | `PaymentMethod` enum | yes | closed set per `models.orders.PaymentMethod` |
| `amount` | `Decimal` | yes | `>= 0` |
| `fee_amount` | `Decimal` | no (default `0`) | `>= 0` |
| `reference` | `str \| None` | no | <= 100 chars |
| `paid_at` | `datetime \| None` | no (default server `now()`) | tz-aware UTC |

**Response (201 Created):** `OrderResponse` (見下方 Pydantic Schemas)

**Behaviour:**

1. 建立 `orders` row with `status=open`，`tenant_id` 自 session context 取得（MVP：從 store 解析 tenant）。
2. 為每一筆 `lines[i]` 寫入 `order_lines` row，`line_total = qty * unit_price`（內部 `Decimal`，不要 round 直到落 DB）。
3. 對每一筆 line：呼叫 `bom_consumer.expand_consumption(line)` (calc-engine 模組，已另由 calc-engine 規格定義) 取得 `list[(ingredient_id, qty_consumed)]`；寫入對應 `stock_movements` row，`movement_type='sale_consume'`，`qty = -qty_consumed`，`source_table='order_lines'`，`source_id=line.id`，`occurred_at=opened_at`。
4. 寫入 `order_discounts` / `order_payments`（如有）。
5. 一切包在同一個 transaction；任何一步失敗 rollback。

**Idempotency:**

- 若 request 含 `external_pos_id` 且該 `(tenant_id, store_id, external_pos_id)` 已存在一筆 `orders` row，**直接回該既有 order 的 `OrderResponse`，HTTP 200**，**不**重複建立、**不**重複寫 stock_movements。
- 若 `external_pos_id` 不同但 `(store_id, business_date, order_no)` 重複，回 409。

### GET /orders/{order_id}

讀取單筆訂單，含 `lines` / `discounts` / `payments` 三個集合。
404 if not found 或 `deleted_at IS NOT NULL`。

### POST /orders/{order_id}/close

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `closed_at` | `datetime \| None` | no | server `now()` | tz-aware UTC |

**Behaviour:**

1. Load order; 若 `status != 'open'` 回 409。
2. 呼叫 `discount_resolver.compute_net_revenue(order)` (calc-engine 模組) 取得 `net_revenue: Decimal`。
3. UPDATE `orders SET status='closed', closed_at=:closed_at`. **不寫入** `net_revenue` 欄位（schema 沒這欄；由 `mv_daily_pnl` 在 read 端聚合）。
4. 若 `SUM(payments.amount)` ≠ resolved net_revenue → 不擋（回 200 + warning header `X-Payment-Mismatch: true`），但記 `notes`。MVP 不擋，留待後續校驗 job。
5. Response 200 + 完整 `OrderResponse`（含 `closed_at`、`status='closed'`）。

### POST /orders/{order_id}/void

| Request field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `reason` | `str` | yes | — | 1..200 chars |

**Behaviour (CRITICAL):**

1. Load order; 若 `status == 'voided'` 回 409；若 `status == 'open'` 也允許作廢（沒結帳就取消）。
2. 對每一筆 **existing** `stock_movements` where `source_table='order_lines' AND source_id IN (order's line ids)`：寫入一筆新的反向 movement，`qty = -original.qty`（負負得正，把扣掉的料補回），`movement_type='adjustment_in'`，`source_table='order_voids'`，`source_id=order.id`，`note='void reversal of movement {orig.id}; reason={reason}'`。**禁止** UPDATE 或 DELETE 既有 stock_movements row。
3. UPDATE `orders SET status='voided'`. `closed_at` 維持原值（若有）。
4. Response 200 + `OrderResponse`。

---

## Pydantic Schemas

所有 input model：`ConfigDict(frozen=True, strict=True)`；所有 `Decimal` 欄位拒 float 輸入。
Response model：`ConfigDict(from_attributes=True)`；timestamps 以 `Asia/Taipei` zone-aware ISO8601 字串輸出。

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class OrderLineCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    menu_item_id: UUID
    qty: Decimal = Field(gt=Decimal("0"))
    unit_price: Decimal = Field(ge=Decimal("0"))
    notes: str | None = Field(default=None, max_length=200)

class OrderDiscountCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    kind: Literal["percent", "amount", "comp", "allowance", "employee"]
    value: Decimal
    reason: str | None = Field(default=None, max_length=200)
    applied_by: UUID | None = None

class OrderPaymentCreate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    method: Literal["cash","credit","linepay","applepay","jko","ubereats","foodpanda","voucher","other"]
    amount: Decimal = Field(ge=Decimal("0"))
    fee_amount: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    reference: str | None = Field(default=None, max_length=100)
    paid_at: datetime | None = None

class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    store_id: UUID
    order_no: str = Field(min_length=1, max_length=64)
    business_date: date
    opened_at: datetime | None = None
    external_pos_id: str | None = Field(default=None, max_length=64)
    pos_source: Literal["ichef","posplus","manual"] = "manual"
    invoice_number: str | None = Field(default=None, pattern=r"^[A-Z]{2}[0-9]{8}$")
    carrier_type: Literal["mobile","citizen","member","paper"] | None = None
    carrier_id: str | None = Field(default=None, max_length=64)
    buyer_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    notes: str | None = Field(default=None, max_length=1000)
    lines: list[OrderLineCreate] = Field(default_factory=list)
    discounts: list[OrderDiscountCreate] = Field(default_factory=list)
    payments: list[OrderPaymentCreate] = Field(default_factory=list)

class OrderCloseRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    closed_at: datetime | None = None

class OrderVoidRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    reason: str = Field(min_length=1, max_length=200)

# --- responses ---

class OrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    menu_item_id: UUID
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal
    cogs_actual: Decimal | None
    cogs_theoretical: Decimal | None
    notes: str | None

class OrderDiscountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    value: Decimal
    reason: str | None
    applied_by: UUID | None

class OrderPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    method: str
    amount: Decimal
    fee_amount: Decimal
    reference: str | None
    paid_at: datetime  # rendered Asia/Taipei

class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    store_id: UUID
    tenant_id: UUID
    order_no: str
    business_date: date
    opened_at: datetime         # Asia/Taipei
    closed_at: datetime | None  # Asia/Taipei
    status: Literal["open","closed","voided","refunded"]
    invoice_number: str | None
    carrier_type: str | None
    carrier_id: str | None
    buyer_tax_id: str | None
    external_pos_id: str | None
    pos_source: str | None
    notes: str | None
    lines: list[OrderLineResponse]
    discounts: list[OrderDiscountResponse]
    payments: list[OrderPaymentResponse]
```

---

## Database writes

| Action | Tables written | Notes |
|---|---|---|
| `POST /orders` (new) | `orders` (1), `order_lines` (N), `order_discounts` (M), `order_payments` (K), `stock_movements` (sum over lines of #ingredients) | one txn |
| `POST /orders` (idempotent replay) | none | returns existing row |
| `POST /close` | `orders` (UPDATE status, closed_at) | no ledger writes |
| `POST /void` | `orders` (UPDATE status), `stock_movements` (N reversing rows, INSERT only) | **never UPDATE/DELETE** existing movement rows |

每一筆 stock_movement reversal 都帶 `note` 引用原始 movement id，方便 audit。

---

## Error responses

| Status | Trigger | Body |
|---|---|---|
| 400 | malformed body (non-JSON, etc) | `{"detail": "..."}` |
| 404 | order_id not found / soft-deleted | `{"detail": "order not found: {id}"}` |
| 409 | already closed (on /close) / already voided (on /void) / `order_no` collides | `{"detail": "...", "code": "conflict_state"}` |
| 409 | `invoice_number` already used by another order | `{"detail": "invoice already issued", "code": "invoice_conflict"}` |
| 422 | Pydantic validation failure（含 float 拒收、tz-naive datetime、negative qty、percent value 超界） | FastAPI 預設格式 |
| 422 | `invoice_number` fails `uniform_invoice_validator` | `{"detail": "invalid invoice format"}` |
| 500 | DB integrity / unexpected | generic |

Idempotency replay **不**算 conflict — 回 200 + existing OrderResponse。

---

## Acceptance Criteria

> 每一條對應一個 pytest test function；命名 `test_orders_ac_NN_*`。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | Create empty order | `POST /orders` with only `store_id`、`order_no`、`business_date` → 201；DB 有 1 row in `orders`，0 in `order_lines`/`order_payments`/`stock_movements`。 |
| AC-2 | Create order with lines triggers BOM | 1 line(menu_item_id=X, qty=2)，X 的 recipe 有 3 個 ingredients → `stock_movements` 出現 3 筆 `sale_consume`、`qty<0`、`source_table='order_lines'`。 |
| AC-3 | line_total computed | unit_price=`Decimal("150")`、qty=`Decimal("2")` → DB 中 `order_lines.line_total=300.0000`。 |
| AC-4 | Idempotent replay by external_pos_id | 相同 `external_pos_id` 第二次 POST → 200（**非** 201），DB 無新 row，`stock_movements` 無新增。 |
| AC-5 | Close order moves status | `POST /close` → response `status='closed'`、`closed_at` 非 null、200。 |
| AC-6 | Cannot close twice | 已 closed 的單再 close → 409 `conflict_state`。 |
| AC-7 | Void writes reversal rows, never deletes | open 單有 1 line（產生 3 筆 sale_consume movements），void 後：`stock_movements` count = 6（3 原 + 3 反向），原 3 筆 row 完整存在、`qty` 未被改。 |
| AC-8 | Voiding a closed order keeps closed_at | closed order void → `status='voided'`，但 `closed_at` 維持原值。 |
| AC-9 | Invoice number validation | `invoice_number='AB12345678'` 通過；`'AB1234'` 422；`'ab12345678'` 422。 |
| AC-10 | Buyer tax id format | 8 digits required；`'1234567'` 422，`'12345678'` 通過。 |
| AC-11 | Percent discount in 0..1 | `kind=percent, value=Decimal("0.5")` 通過；`value=Decimal("1.5")` 422。 |
| AC-12 | Float rejected (strict mode) | `qty=1.5` (float) 422；`qty="1.5"` 透過 Pydantic Decimal 轉換通過。 |
| AC-13 | Negative qty rejected | `qty=Decimal("-1")` 422 (gt=0)。 |
| AC-14 | Decimal precision preserved | `unit_price=Decimal("12.3456")` → DB 中 `Numeric(14,4)` 完整保留 4 dp。 |
| AC-15 | tz-naive datetime rejected | `opened_at='2025-05-01T10:00:00'` (no tz) → 422。 |
| AC-16 | Asia/Taipei in response | DB 存 UTC，response `opened_at` 後綴 `+08:00`。 |
| AC-17 | order_no uniqueness | 同 store + same `business_date` + same `order_no` 第二次 POST 且**無** `external_pos_id` → 409。 |

---

## Tests

- 檔案位置：`tests/routers/test_orders_router.py`
- 框架：`pytest` + `pytest-asyncio` + FastAPI `TestClient` (sync) **OR** `httpx.AsyncClient` (async)
- DB：async test fixture，每個 test function 用 `nested transaction + rollback` 隔離（不要 truncate）。Fixture 路徑 `tests/conftest.py` 提供 `async_session`、`client`、`seeded_store`、`seeded_menu_item_with_recipe`。
- 對 calc-engine 的依賴 (`bom_consumer`、`discount_resolver`) 在 router unit test 階段 stub 掉；另在 integration test 階段裝回真實實作。
- Coverage 目標：所有 AC + happy path + 每個錯誤碼路徑至少 1 test。

---

## Out of scope

- **Authentication / authorization**：Phase 2（目前所有 endpoints 視為已通過上游 gateway 認證）。
- **Multi-tenant**：Phase 2；MVP 假設只有 1 個 tenant，`tenant_id` 由 store 反查或從固定 default 取。
- **Pagination on GET /orders/{id}**：N/A（單筆讀取）；列表型 GET (filter by date range) **延後**到需要時再加，本 spec 不含。
- **Websocket / SSE updates**：Phase 2+。
- **Refund flow**（status='refunded'）：另開 spec。
- **Partial void / 退一筆 line**：另開 spec。
- **服務費 / 稅額**：MVP 由呼叫端在 `unit_price` 上含稅；之後另加 `tax_amount` 欄位（schema 已預留）。
- **Net revenue / discount_total 落 DB**：MVP 由 `mv_daily_pnl` 聚合，不在 orders 表寫入。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `bom_consumer` (calc-engine spec) | router 在每筆 line 建立時呼叫，回傳 list of (ingredient_id, qty) |
| `discount_resolver` (calc-engine spec) | router 在 `POST /close` 呼叫，回傳 net_revenue |
| `uniform_invoice_validator` (existing spec) | router 在 `invoice_number` 欄位驗證時呼叫，422 if invalid |
| `cost_events_router` | 共用 `stock_movements` ledger 寫入慣例（append-only + signed qty）|
| `stock_intake_router` | 共用 `stock_movements` ledger；orders 是負向、purchases 是正向 |
| `mv_daily_pnl` (DB view in `docs/04_data_schema.md §7`) | 下游 read 端，本 router 不直接 query 它 |
| `restaurant_api/main.py` | router 透過 `app.include_router(orders.router)` 掛載 |

— end of spec —
