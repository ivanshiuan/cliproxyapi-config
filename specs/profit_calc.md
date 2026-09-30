---
id: profit_calc
title: Real Profit Calculator (MVP Module)
module: profit_calc
kind: pure-function
status: implemented
preferred_model: sonnet
budget_usd: 5.0
tags: [finance, mvp, pure-function]
ac_count: 15
---

# Task Brief: Real Profit Calculator (MVP Module)

> **Module name:** `real_profit_calculator`
> **Owner domain:** Restaurant / Finance
> **Status:** Specified, ready for PM → Architect → Coder → QA
> **Single deliverable:** one Python module + one pytest file (see Constraints)

---

## 1. Background

台灣餐飲業普遍無法準確掌握「老闆口袋裡真正剩下的錢」。市面上的 POS / ERP 系統雖然提供「表面營收」(surface revenue) 報表，但對於下列**隱形成本**幾乎沒有量化能力：

- 招待 (comp)、折扣 (discount)、折讓 (allowance) — 表面上是收入，實際上是被吃掉的毛利。
- 報廢 (waste)、員工餐 (staff meal)、試吃 (tasting) — 食材成本已經發生，但不會出現在訂單。
- 固定成本攤提 (rent, utilities, depreciation) — 月結報表才看得到，老闆每天無感。
- 平台手續費 (UberEats / foodpanda / LINE Pay)、人事成本 — 沒有逐日對照淨利的工具。

結果：營業額看起來好，月底結算卻沒錢；或是某幾天毛利被偷走，老闆完全不知道。

本模組是「真實獲利計算器」的**第一塊純運算積木**。輸入是當日結構化資料（訂單、折扣、成本事件、固定成本、人事費），輸出是完整的真實 P&L（含真實毛利、真實淨利）並標記**理論 vs 實際成本異常**。所有 I/O、DB、HTTP 都不在本模組內 — 它是純函式 (pure function)。

---

## 2. Goal

提供一個純 Python 模組，給定一日結構化輸入（orders、discounts、comp/waste/staff-meal 事件、fixed-cost 行、COGS 數據），回傳完整的真實 P&L 拆解，包含 `真實毛利 (gross_profit_real)` 與 `真實淨利 (net_profit_real)`；當「實際 COGS」與「理論 COGS」差異 > 淨營收的 5% 時，回傳 `cogs_variance_flag = True` 作為異常訊號。

---

## 3. Scope

### 3.1 In scope (本次 MVP，單一模組)

- 純函式 P&L 運算 (`compute_daily_pnl`)
- Pydantic v2 輸入 / 輸出模型
- 全程使用 `decimal.Decimal`（不允許 float）
- COGS 變異門檻偵測 (5% of net_revenue)
- ≥ 12 條可測試的 acceptance criteria，含正常路徑與邊界情境

### 3.2 Out of scope (延後到後續模組)

- DB I/O（呼叫端自行準備 dict 或 Pydantic 模型，本模組不查 DB）
- HTTP / API 層（沒有 FastAPI，沒有 endpoint）
- 多日聚合（單日輸入，單日輸出）
- 多幣別（僅 TWD）
- 稅務計算（5% 營業稅在發票模組處理，不在本模組）
- 日誌、metrics、observability（呼叫端決定）
- 在地化 / 多語系（純數字運算）

---

## 4. Inputs — Pydantic schemas

下列是**錨定形狀** (anchor shape)。Architect 可在不改變語意的前提下微調命名/驗證細節，但欄位語意與型別不得更動。

```python
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict


class OrderLine(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    item_id: str
    qty: Decimal                  # > 0
    unit_price: Decimal           # menu price, pre-discount, TWD, >= 0
    cogs_actual: Decimal          # actual ingredient cost consumed (from ledger), >= 0
    cogs_theoretical: Decimal     # standard recipe cost, >= 0


class Discount(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    order_id: str
    kind: Literal["percent", "amount", "comp", "allowance", "employee"]
    value: Decimal                # percent: 0..1 (inclusive); amount/comp/allowance/employee: TWD >= 0


class CostEvent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    kind: Literal["waste", "staff_meal", "tasting"]
    cost: Decimal                 # ingredient cost value, >= 0


class FixedCostLine(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    name: str                     # e.g. "rent", "utilities", "depreciation_kitchen"
    daily_amount: Decimal         # already prorated to per-day, >= 0


class PlatformFee(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    source: str                   # e.g. "ubereats", "foodpanda", "linepay"
    amount: Decimal               # >= 0


class DailyPnLInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    business_date: date
    store_id: str
    orders: list[OrderLine]
    discounts: list[Discount]
    cost_events: list[CostEvent]
    platform_fees: list[PlatformFee]
    fixed_costs: list[FixedCostLine]
    labor_cost: Decimal           # day's total labor cost, >= 0
```

### Validation rules (Pydantic field validators)

| Field | Rule |
|---|---|
| `OrderLine.qty` | must be `> 0`; reject `<= 0` |
| `OrderLine.unit_price`, `cogs_actual`, `cogs_theoretical` | `>= 0` |
| `Discount.value` when `kind == "percent"` | `0 <= value <= 1` |
| `Discount.value` when `kind != "percent"` | `>= 0` (TWD amount) |
| `CostEvent.cost`, `FixedCostLine.daily_amount`, `PlatformFee.amount`, `DailyPnLInput.labor_cost` | `>= 0` |
| All `Decimal` fields | `strict=True` — float inputs MUST raise `ValidationError` |

---

## 5. Output schema

```python
class DailyPnLOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    business_date: date
    store_id: str

    # 營收
    gross_revenue: Decimal              # sum(qty * unit_price) across orders
    discount_total: Decimal             # sum of all discount values resolved to TWD
                                        # (percent → percent * order_subtotal; amount/comp/allowance/employee → value as TWD)
    net_revenue: Decimal                # gross_revenue - discount_total (MAY be negative; do NOT clamp)

    # 成本明細
    cogs_actual_total: Decimal          # sum(OrderLine.cogs_actual)
    cogs_theoretical_total: Decimal     # sum(OrderLine.cogs_theoretical)
    cost_waste: Decimal                 # sum CostEvent where kind=="waste"
    cost_staff_meal: Decimal            # sum CostEvent where kind=="staff_meal"
    cost_tasting: Decimal               # sum CostEvent where kind=="tasting"
    platform_fee_total: Decimal         # sum PlatformFee.amount
    labor_cost: Decimal                 # echo input
    fixed_cost_total: Decimal           # sum FixedCostLine.daily_amount

    # 利潤
    gross_profit_real: Decimal
        # = net_revenue - cogs_actual_total - cost_waste - cost_staff_meal - cost_tasting
    net_profit_real: Decimal
        # = gross_profit_real - platform_fee_total - labor_cost - fixed_cost_total

    # 異常訊號
    cogs_variance_pct: Decimal
        # = (cogs_actual_total - cogs_theoretical_total) / net_revenue
        # if net_revenue > 0 else Decimal("0")
        # Note: signed (positive => overconsuming ingredients)
    cogs_variance_flag: bool
        # True iff abs(cogs_variance_pct) > Decimal("0.05")
```

### Rounding / quantization

- 所有金額欄位 (`Decimal` 表示 TWD) 在**輸出前**以 `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` 處理。
- `cogs_variance_pct` 量化為 `Decimal("0.0001")`（四位小數，避免 flag 邊界誤判）。
- 內部運算不要提前 quantize；只在組 `DailyPnLOutput` 時量化，避免累積誤差。

---

## 6. Public interface

```python
def compute_daily_pnl(input: DailyPnLInput) -> DailyPnLOutput:
    """Compute one day's real P&L.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises pydantic.ValidationError on malformed input (via the input model itself).
    """
```

模組必須以這個函式為唯一公開 API。其他 helper 全部以 `_` 開頭視為私有。

---

## 7. Discount resolution rules

折扣的 `value` 在 `kind == "percent"` 時是比例 (0..1)；其他都是 TWD 金額。`discount_total` 在輸出時必須是**已轉為 TWD 的總和**。

### 多重折扣套用順序 (per order_id)

當同一個 `order_id` 上有多筆折扣，套用順序固定為：

1. `percent` (依出現順序累加百分比，套用在該 order 的 subtotal 上)
2. `amount`
3. `allowance`
4. `employee`
5. `comp`（最後；comp 等於該 order 剩餘金額，將該 order 的 net 拉到 0）

`comp` 的語意：**完整招待整張 order**。換言之，無論 `comp.value` 寫多少，該 order 的 net 收入歸零，且該 order 的剩餘金額（subtotal − 前面所有折扣）會被計入 `discount_total`。

> 說明：這個規則簡化了 MVP，未來若要支援「部分招待」(line-level comp) 會另開模組。

---

## 8. Acceptance criteria

> 每一條都必須有對應的 pytest test case。Coder 寫測試時用 worked numbers 對照。

| # | 名稱 | 描述 / 驗收條件 |
|---|---|---|
| AC-1 | Happy path | 10 筆 orders 合計 10,000，無折扣；cogs_actual=3,000、waste=200、staff_meal=0、tasting=0、platform_fees=0、labor=1,500、fixed=2,000 → `gross_profit_real=6,800`，`net_profit_real=3,300`，`cogs_variance_flag=False`（假設 theoretical=3,000）。 |
| AC-2 | Percent discount | 1,000 subtotal + 10% percent discount → `discount_total=100`、`net_revenue=900`。 |
| AC-3 | Amount discount | 1,000 subtotal + 100 amount discount → `discount_total=100`、`net_revenue=900`。 |
| AC-4 | Comp 招待 | 500 訂單整張招待 → 該 order 的 `net_revenue` 貢獻為 0；`discount_total` 包含 500。 |
| AC-5 | COGS variance flag fires | actual=4,000、theoretical=3,000、net_revenue=10,000 → `cogs_variance_pct = Decimal("0.1000")`，`flag=True`。 |
| AC-6 | Threshold suppression | actual=3,490、theoretical=3,000、net_revenue=10,000 → variance=4.9%，`flag=False`。 |
| AC-7 | Zero net revenue | 空 `orders` → 所有金額欄位 = `Decimal("0.00")`，`cogs_variance_pct=Decimal("0")`，`flag=False`，**不得**拋出 ZeroDivisionError。 |
| AC-8 | Decimal precision | 所有金額欄位皆為 `Decimal` 且小數位 = 2（`Decimal("0.01")` quantized，`ROUND_HALF_UP`）。 |
| AC-9 | Negative qty rejected | `OrderLine(qty=Decimal("-1"), ...)` 必須在 Pydantic 驗證階段拋 `ValidationError`。 |
| AC-10 | Float input rejected | `OrderLine(..., unit_price=1000.0, ...)` 必須拋 `ValidationError`（strict mode）。字串 `"1000.00"` 應透過 Pydantic Decimal 轉換被接受。 |
| AC-11 | Stacked discounts | 同一 order 同時有 percent(10%) + amount(50) + comp → 套用順序 percent → amount → comp，最後 net 收入 = 0；`discount_total` 為原訂金額。 |
| AC-12 | Platform fee location | 1,000 net_revenue、100 platform fee、300 cogs_actual、其餘為 0 → `gross_profit_real=700`、`net_profit_real=600`（platform fee 不影響 gross）。 |
| AC-13 | Negative net revenue allowed | discount 總和 > gross_revenue → `net_revenue` 為負，**不得** clamp 至 0；下游計算照常進行。 |
| AC-14 | Zero theoretical cogs | 所有 `cogs_theoretical=0`、`cogs_actual>0`、`net_revenue>0` → `cogs_variance_pct` 依公式照算（不會 div-by-zero，因為分母是 `net_revenue` 而非 `cogs_theoretical`）；當 `abs(variance_pct) > 0.05` flag=True。 |
| AC-15 | CostEvent aggregation | 3 筆 waste(各 100)、2 筆 staff_meal(各 50)、1 筆 tasting(200) → `cost_waste=300`、`cost_staff_meal=100`、`cost_tasting=200`。 |

---

## 9. Edge cases (must be enumerated in tests)

- **Empty `orders`**：見 AC-7。
- **`discount_total > gross_revenue`**：見 AC-13，允許負 net_revenue。
- **`net_revenue == 0` 且 `cogs_actual > 0`**：`cogs_variance_pct` 設為 `Decimal("0")`，`flag=False`（保守：無營收即無異常訊號）。
- **`cogs_theoretical == 0` 且 `net_revenue > 0`**：分母是 `net_revenue`，照算（見 AC-14）。
- **Rounding boundary**：variance_pct 恰好 = `Decimal("0.05")` → `flag=False`（嚴格 `>`，不是 `>=`）。
- **重複的 `order_id`**：折扣 list 中允許同一 `order_id` 多筆（stacking, AC-11）；orders list 中的 `item_id` 不要求唯一。
- **Decimal rounding**：在 `compute_daily_pnl` 結尾統一 quantize；不要在中間步驟提前 quantize。

---

## 10. Constraints (hard requirements)

- **檔案結構**：
  - 模組：`real_profit_calculator.py`（單一檔案）
  - 測試：`test_real_profit_calculator.py`（單一檔案，pytest）
- **依賴**：Python 3.12 標準庫 + `pydantic>=2.5`。**禁止** pandas、numpy、其他第三方。
- **數值型別**：所有金錢一律用 `decimal.Decimal`。函式簽章、Pydantic 欄位、回傳值，都不允許出現 `float`。
- **純函式**：`compute_daily_pnl` 不得有 I/O、不得讀寫全域狀態、不得 logging。
- **型別標註**：每個公開 / 私有函式都要有完整 type hints。
- **不可變**：所有 input Pydantic 模型 `model_config = ConfigDict(frozen=True, strict=True)`。
- **錯誤處理**：輸入驗證錯誤透過 Pydantic 自動拋 `ValidationError`，模組內部不需 try/except。
- **無 magic numbers**：`COGS_VARIANCE_THRESHOLD = Decimal("0.05")` 必須是模組層級常數。

---

## 11. Out of scope (重申，避免 Coder drift)

- 持久化 / DB（無 SQLAlchemy、無 connection）
- HTTP / FastAPI / 任何 web framework
- 日誌（呼叫端決定）
- 在地化
- 多幣別 / 匯率
- 營業稅 (VAT) — 在發票模組
- 退款 / 退單 (refund) 流程 — 後續模組
- 部分招待 (line-level comp) — 後續模組
- 多日彙整 / 趨勢圖 — 後續模組

---

## 12. Connection to the broader system

本模組的輸出**最終會餵入** `docs/04_data_schema.md` 中定義的 `mv_daily_pnl` 物化視圖。欄位命名與語意刻意對齊：

| 本模組輸出欄位 | `mv_daily_pnl` 對應欄位 |
|---|---|
| `gross_revenue` | `gross_revenue` |
| `discount_total` | `discount_total` |
| `net_revenue` | `net_revenue` |
| `cogs_actual_total` | `cogs_actual` |
| `cogs_theoretical_total` | `cogs_theoretical` |
| `cost_waste` | `waste_cost` |
| `cost_staff_meal` | `staff_meal_cost` |
| `cost_tasting` | `tasting_cost` |
| `platform_fee_total` | `platform_fees` |
| `labor_cost` | `labor_cost` |
| `fixed_cost_total` | `fixed_cost_daily` |
| `gross_profit_real` | `gross_profit_real` (注意：本模組另外扣除 waste/staff_meal/tasting，較 DB 視圖嚴格；對齊工作留待後續整合層) |
| `net_profit_real` | `net_profit_real` |
| `cogs_variance_flag` | `cogs_variance_flag` |

> **重要**：data schema 文件是「系統最終形狀」的參考，**不是本次硬性約束**。如果本模組的欄位需要與 `mv_daily_pnl` 完全一致，會在後續「DB projection 適配層」處理；現階段以本 brief 為準。

---

## 13. Done = all of:

1. `real_profit_calculator.py` exists, type-checks cleanly, no unused imports.
2. `test_real_profit_calculator.py` 包含 AC-1 ~ AC-15 對應 test functions（命名 `test_ac_01_*`、`test_ac_02_*` …）。
3. `pytest test_real_profit_calculator.py` 全綠。
4. 沒有 float 出現在模組或測試的任何地方（測試輸入若需 1000，使用 `Decimal("1000")` 或 `Decimal("1000.00")`）。
5. `compute_daily_pnl` 的 docstring 含一段最小 usage 範例。

— end of brief —
