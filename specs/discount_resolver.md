---
id: discount_resolver
title: Discount Resolver (MVP Module)
module: discount_resolver
kind: pure-function
status: implemented
preferred_model: sonnet
budget_usd: 5.0
tags: [finance, mvp, pure-function]
ac_count: 17
---

# Task Brief: Discount Resolver (MVP Module)

> **Module name:** `discount_resolver`
> **Owner domain:** Restaurant / Sales
> **Status:** Specified, ready for PM → Architect → Coder → QA
> **Single deliverable:** one Python module + one pytest file (see Constraints)

---

## 1. Background

台灣餐飲店一張訂單上可能同時有：
- 平日 9 折（percent，0.10）
- 折抵券 NT$50（amount）
- 員工 8 折（employee，照 percent 規則計）
- 客訴折讓 NT$100（allowance）
- 老闆說「這桌我招待」(comp，整單清零)

但顧客拿到的收據通常只看到「折扣 - $XXX」一行。老闆要看「真實毛利」就必須把每一種折扣拆出來、按**正確順序**結算，否則 P&L 對不上、員工偷打折也抓不到。

POS 業界沒有統一的「折扣套用順序」。我們在這份 brief 裡**明文約定一套**，把它寫死，所有上下游模組都照這個順序算。

本模組是「折扣結算器」的**純運算心臟**：
- 輸入：訂單 subtotal + 一張折扣 list
- 輸出：net_revenue、discount_total、每一筆折扣解析後的 effective TWD 值

I/O、DB 都不在本模組。`order_discounts` 表的撈取由呼叫端完成。

---

## 2. Goal

提供一個純 Python 函式：給定一筆訂單的 `subtotal` 與 `discounts` list（內容為 `order_discounts` 表的 row 投影），回傳 `net_revenue`、`discount_total`、以及每一筆折扣「按定義順序套用後實際吃掉多少 TWD」的 breakdown。Net revenue 允許為負（折扣超過 subtotal）。

---

## 3. Scope

### 3.1 In scope (本次 MVP，單一模組)

- 純函式 `resolve_discounts(input: DiscountResolveInput) -> DiscountResolveOutput`
- Pydantic v2 輸入 / 輸出模型，全部 `frozen=True, strict=True`
- 全程使用 `decimal.Decimal`
- 明文定義的 4-step 套用順序（見 §7）
- `comp` 把該訂單剩餘金額全部吃掉
- 負 net_revenue 允許，**不**clamp 至 0
- ≥ 12 條 acceptance criteria

### 3.2 Out of scope

- DB 查詢（呼叫端從 `order_discounts` 撈出來，組成 `DiscountRow` list）
- Line-level discount（MVP 只處理 order-level；`order_line_id` 不在本模組視野）
- 多筆 order 聚合（單一 order 的 subtotal/discounts，單一 order 的輸出）
- 折扣審批流程（`approved_by`、`reason` 由呼叫端記錄，本模組不關心）
- 稅金處理（5% 營業稅在發票模組）

---

## 4. Inputs — Pydantic schemas

下列是**錨定形狀**。欄位語意與型別不得更動。

```python
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict


# Mirrors restaurant_api.models.orders.DiscountKind StrEnum.
DiscountKind = Literal["percent", "amount", "comp", "allowance", "employee"]


class DiscountRow(BaseModel):
    """One row from order_discounts, projected to the fields this module needs."""

    model_config = ConfigDict(frozen=True, strict=True)

    kind: DiscountKind
    value: Decimal
        # kind == "percent" or "employee": 0 <= value <= 1 (e.g. 0.10 == 10% off)
        # kind == "amount" / "allowance" / "comp":  value >= 0 (TWD)
        # comp.value is informational only — comp always zeroes the order regardless of value


class DiscountResolveInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    subtotal: Decimal               # >= 0; sum of order_lines.line_total pre-discount
    discounts: list[DiscountRow]    # MAY be empty
```

### Validation rules (Pydantic field validators)

| Field | Rule |
|---|---|
| `DiscountResolveInput.subtotal` | `>= 0` |
| `DiscountRow.value` when `kind in ("percent", "employee")` | `0 <= value <= 1` |
| `DiscountRow.value` when `kind in ("amount", "allowance", "comp")` | `>= 0` |
| All `Decimal` fields | `strict=True` — float inputs MUST raise `ValidationError` |
| Empty `discounts` list | allowed; returns `net_revenue = subtotal, discount_total = 0, breakdown = []` |

---

## 5. Output schema

```python
class ResolvedDiscount(BaseModel):
    """One discount, after stacking-order resolution."""

    model_config = ConfigDict(frozen=True)

    kind: DiscountKind
    original_value: Decimal                  # echo of the input value (percent ratio OR raw TWD)
    effective_twd: Decimal                   # >= 0; the actual TWD this discount removed
    applied_to_subtotal_after_prior: Decimal # the running subtotal this discount was applied against


class DiscountResolveOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    net_revenue: Decimal                     # MAY be negative; do NOT clamp
    discount_total: Decimal                  # >= 0; sum of breakdown[*].effective_twd
    breakdown: list[ResolvedDiscount]        # same length & order as input.discounts
```

### Rounding / quantization

- `effective_twd` 與 `applied_to_subtotal_after_prior` 在**輸出前**以 `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` 處理（TWD 二位小數）。
- `net_revenue`、`discount_total` 同樣 quantize 到 2dp。
- `original_value` 保留原樣，不 quantize（percent 是 ratio，amount 是 TWD，混合 quantize 會破壞語意）。
- 內部運算不要提前 quantize；只在組裝 output 時量化。

---

## 6. Public interface

```python
def resolve_discounts(input: DiscountResolveInput) -> DiscountResolveOutput:
    """Resolve a stack of order discounts to net revenue.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises pydantic.ValidationError on malformed input.
    """
```

模組必須以這個函式為唯一公開 API。其他 helper 全部以 `_` 開頭視為私有。

---

## 7. Stacking rules (the contract)

折扣套用順序固定為以下 4 步驟。Architect 與 Coder **不得**重新解釋：

| Step | Kind | 套用順序 | 行為 |
|---|---|---|---|
| 1 | `percent` | input order | 對當前 running subtotal 複利套用 ratio |
| 2 | `employee` | input order | **同 percent**（0..1 ratio，複利） |
| 3 | `amount` | input order | 對當前 running subtotal 扣 TWD |
| 4 | `allowance` | input order | 對當前 running subtotal 扣 TWD |
| 5 | `comp` | input order | **不管 value 多少**，直接把當前 running subtotal 全數吃掉，net 歸 0；effective_twd = max(running_subtotal, 0)；剩下的 comp / 其他折扣對 net_revenue 無影響 |

**為什麼 comp 放最後**：comp 在語意上是「老闆說這單我請」，必須在所有其他折扣之後決定還剩多少要吃掉。如果先 comp 再 percent，數字會少打。

**多筆 comp 處理**：第一筆 comp 已經把 running_subtotal 拉到 0，後續 comp 的 `effective_twd = Decimal("0")`，但仍出現在 breakdown（保留 audit 軌跡）。

**負 running_subtotal 進 comp**：若 amount + allowance 加總超過 subtotal，running_subtotal 已為負；comp 的 `effective_twd = Decimal("0")`（不可能補回負值），net_revenue 維持負數。

---

## 8. Acceptance criteria

> 每一條都必須有對應的 pytest test case。Coder 寫測試時用 worked numbers 對照。

| # | 名稱 | 描述 / 驗收條件 |
|---|---|---|
| AC-1 | Single percent | subtotal=1000、percent(0.10) → net_revenue=900、discount_total=100、breakdown 一筆 `effective_twd=100, applied_to_subtotal_after_prior=1000`。 |
| AC-2 | Single amount | subtotal=1000、amount(100) → net_revenue=900、discount_total=100。 |
| AC-3 | Single allowance | subtotal=1000、allowance(50) → net_revenue=950、discount_total=50、breakdown 標 `kind="allowance"`。 |
| AC-4 | Single comp | subtotal=500、comp(0)（value 可為 0） → net_revenue=0、discount_total=500、breakdown[0].effective_twd=500。 |
| AC-5 | Comp ignores value | subtotal=500、comp(value=Decimal("999")) → 仍然 net_revenue=0、effective_twd=500（**不**是 999）。 |
| AC-6 | Two percents compound | subtotal=1000、percent(0.10)、percent(0.05) → 第一筆 effective=100、第二筆 effective=45（不是 50）；net_revenue=855；discount_total=145。 |
| AC-7 | Employee == percent | subtotal=1000、employee(0.20) → net_revenue=800、breakdown[0].kind="employee"、effective_twd=200。混合 `[percent(0.10), employee(0.10)]` 與 `[percent(0.10), percent(0.10)]` 結果相同。 |
| AC-8 | Stacked percent + amount | subtotal=1000、percent(0.10)、amount(50) → step1 後 900、step3 後 850；net_revenue=850；breakdown 順序與 input 相同，percent.effective=100, amount.effective=50。 |
| AC-9 | Stacked percent + amount + comp | subtotal=1000、percent(0.10)、amount(50)、comp() → 900 → 850 → 0；net_revenue=0；discount_total=1000；breakdown 三筆 effective: 100, 50, 850。 |
| AC-10 | Comp ordering invariance | input `[comp(), percent(0.10)]` 與 `[percent(0.10), comp()]` 都應產出 net_revenue=0、discount_total=subtotal；breakdown 順序保留 input 順序，但 effective_twd 對應到正確 step（comp 一律最後被「結算」，即使排在前面）。 |
| AC-11 | Negative net revenue allowed | subtotal=1000、amount(1500) → net_revenue=`Decimal("-500.00")`；**不**clamp 至 0；discount_total=1500；breakdown[0].effective_twd=1500、applied_to_subtotal_after_prior=1000。 |
| AC-12 | Two amounts both apply | subtotal=1000、amount(200)、amount(300) → 800 → 500；net_revenue=500；discount_total=500。 |
| AC-13 | Decimal precision (compound rounding) | subtotal=`Decimal("99.99")`、percent(`Decimal("0.07")`) → effective=`Decimal("7.00")` (`99.99 * 0.07 = 6.9993` → ROUND_HALF_UP → 7.00)；net_revenue=`Decimal("92.99")`。所有金額 2dp。 |
| AC-14 | Empty discounts | subtotal=1000、discounts=[] → net_revenue=1000、discount_total=0、breakdown=[]；**不**拋例外。 |
| AC-15 | Float input rejected | `DiscountRow(kind="percent", value=0.1)`（float）必須拋 `ValidationError`。字串 `"0.10"` 可被接受。 |
| AC-16 | Percent out-of-range rejected | `DiscountRow(kind="percent", value=Decimal("1.5"))` 必須拋 `ValidationError`。 |
| AC-17 | Multiple comps | subtotal=500、comp()、comp() → net_revenue=0；breakdown[0].effective=500、breakdown[1].effective=0；discount_total=500（不是 1000，因為第二筆 comp 沒東西可吃）。 |

---

## 9. Edge cases (must be enumerated in tests)

- **空 `discounts`**：見 AC-14，回傳原 subtotal 為 net_revenue。
- **`subtotal == 0`**：所有 percent/amount 都不會吃到任何東西；effective_twd 皆 = 0；comp 也是 0。
- **`discount_total > subtotal`**：見 AC-11，net_revenue 可為負。
- **多筆 comp**：見 AC-17，第二筆以後的 comp 在 breakdown 仍出現，但 effective_twd=0。
- **percent(0)**：合法輸入；effective_twd=0、running_subtotal 不變。
- **percent(1)**：合法輸入；單一 percent(1) 等於 100% off，net_revenue=0（不需要 comp）。
- **input 順序混雜**：`[amount, percent, comp, percent, amount]` → percent group 先計算（兩筆，compound），再 amount group（兩筆），再 comp。breakdown 順序依 **input 順序** 輸出，但每筆的 `applied_to_subtotal_after_prior` 反映該 kind group 內套用時的 running subtotal。
- **`applied_to_subtotal_after_prior` 為負**：當 amount/allowance 把 subtotal 扣成負後，下一筆同類折扣的 `applied_to_subtotal_after_prior` 會是負數。允許，照實 quantize 後輸出。

---

## 10. Constraints (hard requirements)

- **檔案結構**：
  - 模組：`discount_resolver.py`（單一檔案）
  - 測試：`test_discount_resolver.py`（單一檔案，pytest）
- **依賴**：Python 3.12 標準庫 + `pydantic>=2.5`。**禁止** pandas、numpy、其他第三方。
- **數值型別**：所有金錢一律用 `decimal.Decimal`；不允許 `float`。
- **純函式**：`resolve_discounts` 不得有 I/O、不得讀寫全域狀態、不得 logging。
- **型別標註**：每個公開 / 私有函式都要有完整 type hints。
- **不可變**：所有 Pydantic 模型 `model_config = ConfigDict(frozen=True, strict=True)`（output models 用 `frozen=True` 即可）。
- **錯誤處理**：輸入驗證錯誤透過 Pydantic 自動拋 `ValidationError`，模組內部不需 try/except。
- **無 magic numbers**：套用順序常數應是 module-level（例如 `STACKING_ORDER = ("percent", "employee", "amount", "allowance", "comp")`）。
- **No `eval` / `exec`**。

---

## 11. Out of scope (重申，避免 Coder drift)

- 持久化 / DB（無 SQLAlchemy）
- HTTP / FastAPI
- Line-level discount（後續模組）
- 折扣審批 / `approved_by`
- 稅金（發票模組）
- 退款 / refund

---

## 12. Connection to the broader system

本模組輸出餵入 `mv_daily_pnl` 物化視圖（見 `docs/04_data_schema.md`）的 `discount_total` 與 `net_revenue` 欄位。每一筆 `ResolvedDiscount.effective_twd` 對應 `order_discounts.amount_applied` 欄位（schema 是 `numeric(14,4)`，本模組 quantize 至 2dp 是 TWD presentation 慣例；如需 4dp 精度，由「DB projection 適配層」反轉量化）。

| 本模組輸出欄位 | DB schema 對應欄位 |
|---|---|
| `net_revenue` | (orders.total 的一部分；扣除 discount_total 後) |
| `discount_total` | `orders.discount_total` |
| `ResolvedDiscount.effective_twd` | `order_discounts.amount_applied` |
| `ResolvedDiscount.kind` | `order_discounts.kind` (DiscountKind enum value) |

呼叫端流程（不在本模組）：

1. POS 收到結帳請求，撈出該 order 的 `subtotal` 與所有 `order_discounts`
2. 組 `DiscountResolveInput` 呼叫 `resolve_discounts`
3. 把 `breakdown` 對應寫回 `order_discounts.amount_applied`
4. `net_revenue` 用於 `orders.total = net_revenue + service_charge + tax_amount`

---

## 13. Done = all of:

1. `discount_resolver.py` exists, type-checks cleanly, no unused imports.
2. `test_discount_resolver.py` 包含 AC-1 ~ AC-17 對應 test functions（命名 `test_ac_01_*`、`test_ac_02_*` …）。
3. `pytest test_discount_resolver.py` 全綠。
4. 沒有 float 出現在模組或測試的任何地方。
5. `resolve_discounts` 的 docstring 含一段最小 usage 範例與套用順序摘要。

---

## 14. 給 PM Agent 的提醒

- **套用順序是合約，不是建議**：餐廳老闆對「折扣怎麼算」有極強的直覺反應（員工 8 折 + 滿千折百，他預期先打折再扣百），若 Coder 改成「先扣百再打折」，結帳金額相差 50–100 TWD/張，會直接被現場炸出來。請在 review 時死守 §7。
- **comp 的語意**：在真實世界，老闆說「這單我請」是 atomic 動作，**不**會說「請 80%」。所以 comp.value 在本模組完全是裝飾性的，存在只是為了 `order_discounts.value` 欄位非 nullable。
- **負 net_revenue 不 clamp 的理由**：餐廳的折讓單常常事後補開（例如下個月把退費當折讓記在某張舊訂單上）；對下游 P&L 而言，這張訂單真的有「淨虧損」，clamp 至 0 會丟掉這個訊號。
- **複利 vs 加總百分比**：兩筆 10% 折扣是 `1 - 0.9*0.9 = 19%` off，**不是** 20% off。`19 vs 20` 差 1pp 在大訂單上就是百元級的差異。AC-6 必測。
- **breakdown 順序保留 input 順序**：即使 comp 邏輯上「最後結算」，輸出 breakdown 仍按 input 順序，方便 caller 寫回 `order_discounts` 表時不需重新排序。`applied_to_subtotal_after_prior` 才是真正反映 step 順序的欄位。

— end of brief —
