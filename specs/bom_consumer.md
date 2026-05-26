# Task Brief: BOM Consumer (MVP Module)

> **Module name:** `bom_consumer`
> **Owner domain:** Restaurant / Inventory
> **Status:** Specified, ready for PM → Architect → Coder → QA
> **Single deliverable:** one Python module + one pytest file (see Constraints)

---

## 1. Background

餐廳賣出一份餐點，廚房就消耗了一組固定的食材：一份「義大利肉醬麵」用掉 180g 義大利麵、120g 牛絞肉、80g 番茄醬汁、5g 香料。這個對應關係就是 BOM (Bill of Materials)，在我們的資料模型裡是 `recipes` 表，每一列描述「一份 menu_item 用掉多少 ingredient」。

在 POS 開單 → 餐點 fired → 出餐這個流程中，系統必須**自動**把對應的食材數量從庫存帳本 (`stock_movements`) 扣掉，並記下每一筆扣料事件的「理論成本」(standard cost)。沒做這件事的餐廳就只能靠月底盤點對帳，發現少了三公斤牛肉時，案發現場早就涼了。

本模組是「BOM 自動扣料」的**純運算心臟**：
- 輸入：一筆訂單明細 (menu_item + qty) + 目前有效的配方 list
- 輸出：應該追加到 ledger 的 `StockMovement` deltas，以及這份訂單明細的理論 COGS

I/O、DB 查詢、ledger 寫入都由呼叫端處理。本模組不知道 DB 存在。

---

## 2. Goal

提供一個純 Python 函式：給定 `(menu_item_id, qty_sold, recipes)`，回傳該訂單明細應產生的 `StockMovementDelta` 列表（每筆對應一個 ingredient，數量為負號 = 出庫）與理論 COGS 加總。呼叫端負責把這份 list append 到 `stock_movements` 表。

---

## 3. Scope

### 3.1 In scope (本次 MVP，單一模組)

- 純函式 `consume_bom(input: BOMConsumeInput) -> BOMConsumeOutput`
- Pydantic v2 輸入 / 輸出模型，全部 `frozen=True, strict=True`
- 全程使用 `decimal.Decimal`
- 用 `qty_per_serving × qty_sold` 計算每個 ingredient 的扣料量
- 用 `standard_unit_cost × deducted_qty` 計算理論成本
- 缺少配方時拋出 `MissingRecipeError`（domain exception）
- ≥ 10 條 acceptance criteria

### 3.2 Out of scope

- DB 查詢（呼叫端從 `recipes` 表撈出 active rows 後注入）
- ledger 寫入（呼叫端拿到 deltas 後自己 INSERT）
- 配方版本切換（`effective_from` / `effective_to` 的時間判定由呼叫端做完）
- 多店 / tenant 隔離（本模組不認識 `tenant_id` / `store_id`）
- waste、staff_meal、tasting 的 deltas（另有 cost_events 模組處理）
- yield_factor / 損耗率（MVP 不處理，後續模組加）

---

## 4. Inputs — Pydantic schemas

下列是**錨定形狀**。欄位語意與型別不得更動；Architect 可微調命名與驗證細節。

```python
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class RecipeRow(BaseModel):
    """One BOM row, already resolved to the active version by the caller.

    Mirrors restaurant_api.models.inventory.Recipe (qty_per_serving column)
    plus ingredient.standard_cost_per_unit projected from the join.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    ingredient_id: str                  # uuid str — opaque to this module
    qty_per_serving: Decimal            # > 0; in ingredient's unit (g, ml, pcs)
    standard_unit_cost: Decimal         # >= 0; TWD per unit, from ingredients.standard_cost_per_unit


class BOMConsumeInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    menu_item_id: str                   # uuid str
    qty_sold: Decimal                   # > 0 (e.g. 2 for "two plates of pasta")
    recipes: list[RecipeRow]            # active recipes for this menu_item; MAY be empty
```

### Validation rules (Pydantic field validators)

| Field | Rule |
|---|---|
| `RecipeRow.qty_per_serving` | `> 0`; reject `<= 0` |
| `RecipeRow.standard_unit_cost` | `>= 0` |
| `BOMConsumeInput.qty_sold` | `> 0`; reject `<= 0` (zero-qty sales are a bug, not a use case) |
| `BOMConsumeInput.recipes` | list MAY be empty — empty triggers `MissingRecipeError` in `consume_bom`, not at validation time |
| All `Decimal` fields | `strict=True` — float inputs MUST raise `ValidationError` |

---

## 5. Output schema

```python
class StockMovementDelta(BaseModel):
    """One ledger row to be appended by the caller.

    Aligned with restaurant_api.models.inventory.StockMovement:
    - ``qty`` is SIGNED (negative for sale_consume).
    - ``movement_type`` is the string value of the enum (caller maps to
      MovementType in the ORM layer).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    ingredient_id: str
    qty: Decimal                        # < 0 always (sale consumes stock)
    unit_cost: Decimal                  # >= 0; standard_unit_cost echoed for traceability
    movement_type: str = "sale_consume" # literal — caller maps to enum


class BOMConsumeOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    movements: list[StockMovementDelta]
    theoretical_cogs: Decimal           # sum(|qty| * unit_cost) across movements, >= 0
```

### Rounding / quantization

- `StockMovementDelta.qty` 與 `unit_cost` **不** quantize（保留原始 4 位精度，與 `Numeric(14,4)` 一致）。
- `BOMConsumeOutput.theoretical_cogs` 在組裝時 `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`（TWD 二位小數）。
- 內部累加不要提前 quantize；只在輸出 `theoretical_cogs` 時量化。

---

## 6. Public interface

```python
class MissingRecipeError(ValueError):
    """Raised when consume_bom is called with an empty recipes list."""


def consume_bom(input: BOMConsumeInput) -> BOMConsumeOutput:
    """Compute stock_movements deltas for one order line.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises:
        pydantic.ValidationError: malformed input (sign/strict violations).
        MissingRecipeError: input.recipes is empty.
    """
```

模組必須以這個函式為唯一公開 API。其他 helper 全部以 `_` 開頭視為私有。

---

## 7. Computation rules

對每一個 `recipe_row in input.recipes`：

1. `deducted_qty = recipe_row.qty_per_serving * input.qty_sold` （正數）
2. `signed_qty = -deducted_qty`（出庫，記負號）
3. `unit_cost = recipe_row.standard_unit_cost`
4. 產生 `StockMovementDelta(ingredient_id=..., qty=signed_qty, unit_cost=unit_cost, movement_type="sale_consume")`

`theoretical_cogs = sum(abs(delta.qty) * delta.unit_cost for delta in movements)`，最後 `quantize(Decimal("0.01"))`。

**輸出順序**：`movements` 必須保留 `input.recipes` 的順序（穩定排序），方便呼叫端 round-trip 比對。

---

## 8. Acceptance criteria

> 每一條都必須有對應的 pytest test case。Coder 寫測試時用 worked numbers 對照。

| # | 名稱 | 描述 / 驗收條件 |
|---|---|---|
| AC-1 | Single-ingredient happy path | menu_item 配方只有 1 個 ingredient (`qty_per_serving=Decimal("100")`, `standard_unit_cost=Decimal("0.5")`)，`qty_sold=Decimal("1")` → `movements=[delta(qty=Decimal("-100"), unit_cost=Decimal("0.5"))]`、`theoretical_cogs=Decimal("50.00")`。 |
| AC-2 | Multi-ingredient recipe | 3 個 ingredient (`(100, 0.5), (50, 1.0), (5, 2.0)`)，`qty_sold=1` → 3 筆 movements，順序與 input 相同；`theoretical_cogs = 50 + 50 + 10 = Decimal("110.00")`。 |
| AC-3 | Qty multiplier | 同 AC-2 但 `qty_sold=Decimal("3")` → 每筆 movement 的 `qty` 是 AC-2 的 3 倍（仍負號），`theoretical_cogs=Decimal("330.00")`。 |
| AC-4 | Fractional qty_sold | `qty_sold=Decimal("0.5")` (半份)、單 ingredient `qty_per_serving=Decimal("100")` → `movements[0].qty == Decimal("-50")`。 |
| AC-5 | Missing recipe raises | `BOMConsumeInput(recipes=[])` → `consume_bom` 拋 `MissingRecipeError`（不是 `ValidationError`，因為空 list 在輸入階段合法）。 |
| AC-6 | Zero qty_sold rejected | `qty_sold=Decimal("0")` 必須在 Pydantic 驗證階段拋 `ValidationError`。 |
| AC-7 | Negative qty_sold rejected | `qty_sold=Decimal("-1")` 必須在 Pydantic 驗證階段拋 `ValidationError`。 |
| AC-8 | Zero standard_unit_cost | `RecipeRow(standard_unit_cost=Decimal("0"))` 合法；該 ingredient 的 movement 仍會產生（`qty < 0`），但對 `theoretical_cogs` 貢獻為 0。 |
| AC-9 | Decimal precision (high) | `qty_per_serving=Decimal("0.0125")`, `standard_unit_cost=Decimal("80.0000")`, `qty_sold=Decimal("7")` → `movements[0].qty=Decimal("-0.0875")`、貢獻 cogs = `Decimal("7.00")`。`qty` 與 `unit_cost` **不** quantize 至 2 位。 |
| AC-10 | Theoretical COGS quantized to 2dp | 任何浮點 cents 結果（例如 `Decimal("123.456")`）在輸出時須 `quantize(Decimal("0.01"), ROUND_HALF_UP)` → `Decimal("123.46")`。 |
| AC-11 | Float input rejected (strict) | `RecipeRow(qty_per_serving=0.5, ...)`（float）必須拋 `ValidationError`；字串 `"0.5"` 透過 Pydantic Decimal 轉換被接受。 |
| AC-12 | Output immutability | `output.movements.append(...)` 或 `output.movements[0].qty = Decimal("0")` 應因 `frozen=True` / list 是 tuple 化的不可變副本而失敗（pydantic 對 list 預設可變，故 Architect 須用 tuple 或保證測試覆蓋此情境）。 |
| AC-13 | Movements order preserved | recipes input 順序 [A, B, C] → movements 輸出順序也是 [A, B, C]，不得依 ingredient_id 排序。 |
| AC-14 | Movement sign is negative | 對每一筆 `delta in output.movements`：`delta.qty < 0`。 |
| AC-15 | Movement type literal | 對每一筆 `delta in output.movements`：`delta.movement_type == "sale_consume"`。 |

---

## 9. Edge cases (must be enumerated in tests)

- **空 `recipes`**：見 AC-5，raise `MissingRecipeError` 而非回傳空 movements list。理由：靜默的「無扣料」在生產上是配方資料漏建的 bug 信號，必須讓上游知道。
- **單一 ingredient 出現多次**：本模組**不**做合併；如果 caller 不小心傳入兩筆同 `ingredient_id` 的 `RecipeRow`，產生兩筆 `StockMovementDelta`，由 caller 負責。理由：純函式，不替 caller 做去重；ledger 是 append-only，多筆也是合法歷史。
- **`standard_unit_cost == 0`**：見 AC-8。仍產生 movement，因為庫存量必須扣；只是該 movement 的 `theoretical_cogs` 貢獻為 0。
- **大 `qty_sold`**：`qty_sold=Decimal("9999")` 不特殊處理；Decimal 不溢位。
- **超高精度**：見 AC-9。`qty` 與 `unit_cost` 保留原始精度；只有 `theoretical_cogs` 做 2dp quantize。

---

## 10. Constraints (hard requirements)

- **檔案結構**：
  - 模組：`bom_consumer.py`（單一檔案）
  - 測試：`test_bom_consumer.py`（單一檔案，pytest）
- **依賴**：Python 3.12 標準庫 + `pydantic>=2.5`。**禁止** pandas、numpy、SQLAlchemy、任何 DB driver。
- **數值型別**：所有金錢與數量一律用 `decimal.Decimal`；函式簽章、Pydantic 欄位、回傳值不允許出現 `float`。
- **純函式**：`consume_bom` 不得有 I/O、不得讀寫全域狀態、不得 logging。
- **型別標註**：每個公開 / 私有函式都要有完整 type hints。
- **不可變**：所有 Pydantic 模型 `model_config = ConfigDict(frozen=True, strict=True)`（output 用 `frozen=True` 即可）。
- **錯誤處理**：輸入驗證錯誤透過 Pydantic 自動拋 `ValidationError`；空 recipes 拋 `MissingRecipeError`（domain exception，繼承 `ValueError`）。
- **無 magic numbers**：常數一律 module-level 命名，例如 `MOVEMENT_TYPE_SALE_CONSUME = "sale_consume"`。
- **No `eval` / `exec`**。

---

## 11. Out of scope (重申，避免 Coder drift)

- DB I/O（無 SQLAlchemy session）
- HTTP / FastAPI
- 配方版本選擇（呼叫端做完）
- 損耗率 / yield_factor（後續模組）
- 多店 / multi-tenant
- waste / staff_meal / tasting deltas（另有 cost_events 模組）
- ledger 寫入（呼叫端做）

---

## 12. Connection to the broader system

本模組輸出的 `StockMovementDelta` 欄位刻意對齊 `restaurant_api/models/inventory.py` 中的 `StockMovement` ORM：

| 本模組輸出欄位 | `stock_movements` 對應欄位 |
|---|---|
| `ingredient_id` | `ingredient_id` |
| `qty` | `qty` (Numeric(14,4), signed) |
| `unit_cost` | (caller 寫入時可帶上；MVP 的 `StockMovement` ORM 沒有 unit_cost 欄位，會放在 future 補齊) |
| `movement_type="sale_consume"` | `MovementType.SALE_CONSUME` enum value |

呼叫端流程（不在本模組）：

1. POS 收到訂單明細 `(menu_item_id, qty)`
2. 查 `recipes` WHERE `menu_item_id=? AND effective_to IS NULL`，JOIN `ingredients` 帶出 `standard_cost_per_unit`
3. 組成 `BOMConsumeInput`，呼叫 `consume_bom`
4. 對回傳的 `movements`，逐筆 INSERT 到 `stock_movements`（with `tenant_id`, `store_id`, `occurred_at`, `source_table='order_lines'`, `source_id=order_line_id`）
5. 把 `theoretical_cogs` 寫回 `order_lines.cogs_theoretical`

> **重要**：schema 文件是「系統最終形狀」的參考。本模組欄位若與最終 `stock_movements` 有命名差異，由「DB projection 適配層」吸收，本模組保持 brief 內定義。

---

## 13. Done = all of:

1. `bom_consumer.py` exists, type-checks cleanly, no unused imports.
2. `test_bom_consumer.py` 包含 AC-1 ~ AC-15 對應 test functions（命名 `test_ac_01_*`、`test_ac_02_*` …）。
3. `pytest test_bom_consumer.py` 全綠。
4. 沒有 float 出現在模組或測試的任何地方。
5. `consume_bom` 的 docstring 含一段最小 usage 範例。

---

## 14. 給 PM Agent 的提醒

- **為什麼空 recipes 要 raise**：餐廳實務上，菜單上架但配方沒建好，是「上線當天才發現帳本不對」的最常見根因。本模組刻意把這個情境 fail-fast，逼上游 fix data，不要讓 silent zero-cogs 流到 P&L。
- **為什麼不合併同 ingredient**：未來 `recipes` 表會支援「同一道菜兩個來源的同食材」（例如沙拉用了兩種規格的橄欖油但同 `ingredient_id`），合併會丟掉這個歷史。純函式不替上游做合併。
- **為什麼 `qty` 不 quantize**：`qty` 對應 `Numeric(14,4)`，DB 端保留 4 位；如果在本模組強制 2 位，會把 `0.0125g` 香料的扣料抹平成 0。只在 `theoretical_cogs` 量化到 TWD 二位。
- **單元測試的 worked numbers** 要包含「義大利肉醬麵」這種真實情境（180g 麵 + 120g 肉 + 80g 醬汁），這對 Coder 比抽象的 A/B/C 直覺。

— end of brief —
