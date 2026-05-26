# Task Brief: COGS Variance Detector (MVP Module)

> **Module name:** `cogs_variance_detector`
> **Owner domain:** Restaurant / Inventory
> **Status:** Specified, ready for PM → Architect → Coder → QA
> **Single deliverable:** one Python module + one pytest file (see Constraints)

---

## 1. Background

餐廳「理論成本」與「實際成本」的差異 = 隱形漏洞：
- 偷料：員工把食材帶回家。
- 配方亂打：廚師為了討好客人加量，配方表沒更新。
- 報廢沒記：發霉的牛肉被丟掉但沒人按報廢按鈕，於是 ledger 看起來是「正常消耗」。
- 盤點誤差：上個月期初存量算錯。

**理論 COGS** = `Σ (賣出的 menu_item.qty × recipe.qty_per_serving × ingredient.standard_unit_cost)`，從 `recipes` × `order_lines` 算出來。
**實際 COGS** = `Σ stock_movements WHERE movement_type='sale_consume'`（ledger 的扣料事件，用當時的 weighted-average cost）。

兩者差異除以「淨營收」就是「成本變異率」(cogs_variance_pct)。MVP 約定：
- `|variance_pct| < threshold` (預設 5%)：正常 (`ok`)
- `threshold ≤ |variance_pct| < 2 × threshold`：警告 (`warning`)
- `|variance_pct| ≥ 2 × threshold`：嚴重 (`critical`)

本模組的工作就是**判決一個日結果**：吃下當日的 actual / theoretical / net_revenue，輸出一份結構化的異常報告。

I/O、SQL 聚合、寫入 alert 表全部不在本模組。本模組是 dashboard / 排程 job 拉好數據後喂進來的純運算。

---

## 2. Goal

提供一個純 Python 函式：給定一日的 (`actual_cogs`, `theoretical_cogs`, `net_revenue`, `threshold_pct`)，回傳結構化的 `VarianceReport`，包含 absolute / pct variance、`flag` (bool)、`severity` (三檔)、與人類可讀的 `reason` 字串。

---

## 3. Scope

### 3.1 In scope (本次 MVP，單一模組)

- 純函式 `detect_variance(input: VarianceInput) -> VarianceReport`
- Pydantic v2 輸入 / 輸出模型，全部 `frozen=True, strict=True`
- 全程使用 `decimal.Decimal`
- 三級 severity（`ok` / `warning` / `critical`）
- 默認 threshold = 5% (`Decimal("0.05")`)，允許 caller override
- 邊界 inclusive / exclusive 明文約定
- 零營收保護（避免 div-by-zero，回傳 ok + reason="no_revenue"）
- ≥ 10 條 acceptance criteria

### 3.2 Out of scope

- 多日聚合 / 趨勢分析
- 根因分析（哪一個 ingredient 偷得最多）— 後續模組
- 寫入 alert 表 / Slack 通知
- DB 查詢
- 多店比較 / benchmarking

---

## 4. Inputs — Pydantic schemas

下列是**錨定形狀**。

```python
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class VarianceInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    business_date: date
    store_id: str                                # uuid str — echo to output
    actual_cogs: Decimal                         # >= 0; from stock_movements ledger
    theoretical_cogs: Decimal                    # >= 0; from recipes × order_lines × standard_cost
    net_revenue: Decimal                         # MAY be negative or zero; not constrained
    threshold_pct: Decimal = Decimal("0.05")     # > 0; default 5%
```

### Validation rules (Pydantic field validators)

| Field | Rule |
|---|---|
| `actual_cogs`, `theoretical_cogs` | `>= 0` |
| `net_revenue` | unconstrained (negative allowed — discount_total > gross_revenue scenario) |
| `threshold_pct` | `> 0`; reject `<= 0` |
| All `Decimal` fields | `strict=True` — float inputs MUST raise `ValidationError` |

---

## 5. Output schema

```python
from typing import Literal


class VarianceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    business_date: date                          # echo
    store_id: str                                # echo
    variance_abs: Decimal                        # = actual_cogs - theoretical_cogs (SIGNED)
    variance_pct: Decimal
        # = variance_abs / net_revenue, quantized to 4dp (Decimal("0.0001"))
        # if net_revenue == 0 OR net_revenue < 0:  variance_pct = Decimal("0.0000")
    flag: bool                                   # True iff severity in {"warning","critical"}
    severity: Literal["ok", "warning", "critical"]
    reason: str
        # one of:
        #   "ok"                — within threshold
        #   "over_threshold"    — warning band
        #   "critical_overrun"  — actual >> theoretical, critical
        #   "critical_underrun" — actual << theoretical (suspicious: theoretical > actual)
        #   "no_revenue"        — net_revenue <= 0, variance suppressed
```

### Rounding / quantization

- `variance_abs`：`quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`（TWD 2dp）。
- `variance_pct`：`quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)`（4dp，避免邊界誤判 — 例如 4.99% vs 5%）。
- 內部運算不要提前 quantize；只在組裝 output 時量化。

---

## 6. Public interface

```python
def detect_variance(input: VarianceInput) -> VarianceReport:
    """Classify one day's COGS variance.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises pydantic.ValidationError on malformed input.
    """
```

模組必須以這個函式為唯一公開 API。其他 helper 全部以 `_` 開頭視為私有。

---

## 7. Classification rules

### 7.1 Severity bands (based on `|variance_pct|`)

設 `T = threshold_pct`，`v = abs(variance_pct)`：

| Condition | severity | flag | reason |
|---|---|---|---|
| `net_revenue <= 0` | `ok` | `False` | `no_revenue` |
| `v < T` | `ok` | `False` | `ok` |
| `T <= v < 2*T` | `warning` | `True` | `over_threshold` |
| `v >= 2*T` and `variance_abs >= 0` | `critical` | `True` | `critical_overrun` |
| `v >= 2*T` and `variance_abs < 0` | `critical` | `True` | `critical_underrun` |

### 7.2 Boundary discipline

- `v < T` 是 strict less-than：正好 `v == T` 屬於 `warning` 帶。
- `v < 2*T` 是 strict less-than：正好 `v == 2*T` 屬於 `critical` 帶。
- 用 4dp quantize 後比較，避免「4.9999% vs 5%」這種浮點邊界爭議。

### 7.3 為什麼負 variance（actual < theoretical）也算 critical_underrun

直覺：理論成本是「廚房**應該**用掉的食材」，實際成本（從 ledger）是「廚房**真的**扣掉的食材」。
- `actual > theoretical` → 廚房用太多（合理場景：偷料、報廢沒記、配方落後、廚師加料）。
- `actual < theoretical` → 廚房**用得比配方還少**。聽起來像賺到，但更常見的原因是：
  - sale_consume 沒被觸發（POS 整合 bug、銷售沒進 ledger）
  - 員工關閉了某類 movement 寫入
  - 庫存盤點時把虧損用 adjustment_in 偷偷補回，蓋過真實扣料

→ 任何方向的大幅變異都應 flag，符號分流只是讓 reason 字串給人類提示。

### 7.4 零營收 / 負營收保護

`net_revenue <= 0` 時，`variance_pct` **不**計算（會除以零或符號錯亂），直接設為 `Decimal("0.0000")`，severity=`ok`，reason=`no_revenue`。這是**保守設計**：沒營業日（公休）或全部退款日不該觸發成本警告。

---

## 8. Acceptance criteria

> 每一條都必須有對應的 pytest test case。

| # | 名稱 | 描述 / 驗收條件 |
|---|---|---|
| AC-1 | Happy path (ok) | actual=3000、theoretical=3000、net_revenue=10000、threshold=0.05 → variance_abs=0、variance_pct=Decimal("0.0000")、severity="ok"、flag=False、reason="ok"。 |
| AC-2 | Just under threshold | actual=3499、theoretical=3000、net_revenue=10000 → variance_pct = 499/10000 = `Decimal("0.0499")` < 5%，severity="ok"、flag=False。 |
| AC-3 | Exactly at threshold | actual=3500、theoretical=3000、net_revenue=10000 → variance_pct = `Decimal("0.0500")` = 5%，severity="warning"、flag=True、reason="over_threshold"。（邊界 `v == T` 算 warning。） |
| AC-4 | Just over threshold | actual=3501、theoretical=3000、net_revenue=10000 → variance_pct = `Decimal("0.0501")`，severity="warning"。 |
| AC-5 | At double threshold (critical) | actual=4000、theoretical=3000、net_revenue=10000 → variance_pct = `Decimal("0.1000")` = 10% = 2*T，severity="critical"、flag=True、reason="critical_overrun"。 |
| AC-6 | Negative variance critical (underrun) | actual=1000、theoretical=3000、net_revenue=10000 → variance_abs=`Decimal("-2000.00")`、variance_pct=`Decimal("-0.2000")`、`|pct|=20%` >= 2*T → severity="critical"、reason="critical_underrun"、flag=True。 |
| AC-7 | Negative variance just warning | actual=2400、theoretical=3000、net_revenue=10000 → variance_abs=`Decimal("-600.00")`、`|pct|=6%` ∈ [5%, 10%) → severity="warning"、reason="over_threshold"、flag=True。（warning 帶不區分 over/under reason。） |
| AC-8 | Zero net_revenue → ok | actual=3000、theoretical=2000、net_revenue=0 → variance_pct=`Decimal("0.0000")`、severity="ok"、reason="no_revenue"、flag=False。**不**得拋 ZeroDivisionError。 |
| AC-9 | Negative net_revenue → ok | actual=3000、theoretical=2000、net_revenue=`Decimal("-100")` → severity="ok"、reason="no_revenue"、flag=False。 |
| AC-10 | Custom threshold | actual=3000、theoretical=3200、net_revenue=10000、threshold=`Decimal("0.01")` → `|pct|=2%` >= 2*1% → severity="critical"、reason="critical_underrun"。 |
| AC-11 | Zero theoretical_cogs | actual=500、theoretical=0、net_revenue=10000 → variance_abs=500、variance_pct=`Decimal("0.0500")` (recall: 分母是 net_revenue，不是 theoretical) → severity="warning"。**不**拋 div-by-zero（zero theoretical 是合法輸入）。 |
| AC-12 | Decimal precision (4dp variance_pct) | actual=3033、theoretical=3000、net_revenue=10000 → variance_pct = 33/10000 = `Decimal("0.0033")`（4dp 精度），不是 `Decimal("0.00")`。 |
| AC-13 | Float input rejected | `VarianceInput(actual_cogs=3000.0, ...)`（float）必須拋 `ValidationError`。 |
| AC-14 | Threshold must be positive | `threshold_pct=Decimal("0")` 或 `Decimal("-0.05")` 必須拋 `ValidationError`。 |
| AC-15 | Default threshold = 5% | 未提供 threshold_pct → 用 `Decimal("0.05")` 作為門檻；可由模組層級常數 `DEFAULT_THRESHOLD_PCT = Decimal("0.05")` 驗證。 |

---

## 9. Edge cases (must be enumerated in tests)

- **`net_revenue == 0`**：見 AC-8。
- **`net_revenue < 0`**：見 AC-9。Caller 可能因為退款超過營收給負值；本模組保守靜音。
- **`theoretical_cogs == 0`**：見 AC-11。分母是 `net_revenue`，不會 div-by-zero。
- **超大 actual**：actual=`Decimal("1000000")`、theoretical=0、net_revenue=1 → variance_pct 數字會非常大；4dp quantize 不會失敗，severity=critical。
- **Threshold 邊界**：see AC-3 (v=T → warning), AC-5 (v=2T → critical)。**`<` 與 `<=` 寫對。**
- **負 net_revenue 但 actual/theoretical 都 = 0**：仍走 `no_revenue` 路徑，severity="ok"。
- **超高 threshold**：threshold=`Decimal("0.5")` (50%) 合法；幾乎不會 flag，但邏輯不變。

---

## 10. Constraints (hard requirements)

- **檔案結構**：
  - 模組：`cogs_variance_detector.py`（單一檔案）
  - 測試：`test_cogs_variance_detector.py`（單一檔案，pytest）
- **依賴**：Python 3.12 標準庫 + `pydantic>=2.5`。**禁止** pandas、numpy、其他第三方。
- **數值型別**：所有金錢與比率一律用 `decimal.Decimal`；不允許 `float`。
- **純函式**：`detect_variance` 不得有 I/O、不得讀寫全域狀態、不得 logging。
- **型別標註**：每個公開 / 私有函式都要有完整 type hints。
- **不可變**：所有 Pydantic 模型 `model_config = ConfigDict(frozen=True, strict=True)`（output 用 `frozen=True` 即可）。
- **錯誤處理**：輸入驗證錯誤透過 Pydantic 自動拋 `ValidationError`；內部不需 try/except `ZeroDivisionError`（由 `net_revenue <= 0` 分支保護）。
- **無 magic numbers**：`DEFAULT_THRESHOLD_PCT = Decimal("0.05")`、`CRITICAL_MULTIPLIER = Decimal("2")` 必須是 module-level 常數。
- **No `eval` / `exec`**。

---

## 11. Out of scope (重申，避免 Coder drift)

- 多日聚合 / 趨勢圖
- 根因分析（per-ingredient breakdown）
- alert / notification 寫入
- DB / SQL
- 多店比較

---

## 12. Connection to the broader system

本模組輸出對應 `mv_daily_pnl` 的 `cogs_variance_flag` 與（隱含的）variance % 欄位（見 `docs/04_data_schema.md`）。差別在於：

| `mv_daily_pnl` 欄位 | 本模組對應欄位 |
|---|---|
| `cogs_actual` | `input.actual_cogs` |
| `cogs_theoretical` | `input.theoretical_cogs` |
| `net_revenue` | `input.net_revenue` |
| `cogs_variance_amount` (= actual - theoretical) | `variance_abs` |
| `cogs_variance_flag` | `flag` |
| — (mv 沒有 severity 欄位) | `severity` (本模組額外輸出) |

mv 視圖內 hard-coded 0.05 為 threshold；本模組讓 caller 可 override，方便 A/B threshold tuning。Dashboard / 排程 job 在拉完 mv 數據後呼叫本模組，把 `severity` 寫入另一張 `cogs_alerts` 表（不在本 MVP）。

呼叫端流程（不在本模組）：

1. 排程 job 每日凌晨拉 `mv_daily_pnl` 該日該店的一列
2. 投影到 `VarianceInput`，呼叫 `detect_variance`
3. 若 `flag == True`，寫入 alert / 發 Slack

---

## 13. Done = all of:

1. `cogs_variance_detector.py` exists, type-checks cleanly, no unused imports.
2. `test_cogs_variance_detector.py` 包含 AC-1 ~ AC-15 對應 test functions（命名 `test_ac_01_*`、`test_ac_02_*` …）。
3. `pytest test_cogs_variance_detector.py` 全綠。
4. 沒有 float 出現在模組或測試的任何地方。
5. `detect_variance` 的 docstring 含一段最小 usage 範例與三檔 severity 表。

---

## 14. 給 PM Agent 的提醒

- **邊界紀律**：`v == T` 屬 warning、`v == 2T` 屬 critical。Coder 寫 `<` vs `<=` 是這個模組最容易出 bug 的地方，AC-3 / AC-5 必測。
- **負 variance 不是好事**：直覺上「理論用 3000、實際只用 1000、省了 2000」聽起來棒。但餐廳實務上這 99% 是 ledger 漏記，**比偷料更危險**（因為連帳本都失真）。`reason="critical_underrun"` 是給人類看的提示，請在 docstring 與 PR 說明強調。
- **`no_revenue` 路徑的存在感**：公休日、整日全退款日，本模組會吐 `ok + no_revenue`。Dashboard 顯示時要把這個和「真的正常」(`ok + ok`) 區分，由 caller 決定。
- **threshold 預設 5%**：是業界粗略 benchmark；高客單價的店（fine dining）可能要降到 2%、高 SKU 量的便當店可以放到 8%。本模組讓 caller override，不要把 5% 寫死在內部判斷裡。

— end of brief —
