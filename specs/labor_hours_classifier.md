---
id: labor_hours_classifier
title: Labor Hours Classifier (MVP Module)
module: labor_hours_classifier
kind: pure-function
status: implemented
preferred_model: sonnet
budget_usd: 5.0
tags: [staffing, labor-law, pure-function]
ac_count: 19
---

# Task Brief: Labor Hours Classifier (MVP Module)

> **Module name:** `labor_hours_classifier`
> **Owner domain:** Restaurant / HR
> **Status:** Specified, ready for PM → Architect → Coder → QA
> **Single deliverable:** one Python module + one pytest file (see Constraints)

---

## 1. Background

台灣《勞動基準法》(勞基法) 對工時分類有明確規範，餐廳是違規重災區：早班 8 小時 + 留下來幫忙打烊變 12 小時、跨午夜班、國定假日「啊你來上一下班好不好」——每一種情境的時薪倍率都不一樣，算錯一個員工，勞檢來罰款 30 萬起跳。

法規倍率（餐廳 MVP 必須對應的四桶）：
- **正常工時**：每日 ≤ 8 小時，1.0x。
- **延長工時第一級** (overtime tier 1)：第 9–10 小時，1.34x。
- **延長工時第二級** (overtime tier 2)：第 11–12 小時，1.67x。
- **國定假日加班**：在 `public_holidays` 名單上的日子整日按 2.0x，**不**做 tier 切分（勞基法 §39）。
- **絕對上限**：單日 ≤ 12 小時 (含加班)，超過直接違法 — 本模組視為輸入錯誤，拋例外。

跨午夜班（晚上 6 點上班、凌晨 2 點下班）必須按「跨日」拆解：calendar day A 的部分歸 A 日、calendar day B 的部分歸 B 日；兩部分各自獨立套用 8/10/12 切分。若 day A 是平日、day B 是國定假日，day B 的部分整段按 holiday 計算。

本模組是「勞基法工時分桶」的**純運算心臟**：
- 輸入：一筆 clock_in / clock_out timestamp + 國定假日 list
- 輸出：四桶小時數 (regular / OT1 / OT2 / holiday)

I/O、DB、薪資計算（× hourly_rate）都不在本模組。

---

## 2. Goal

提供一個純 Python 函式：給定 `(employee_id, clock_in, clock_out, public_holidays, tz)`，回傳 `LaborBuckets` 四桶小時數（Decimal(6,2)），可被薪資結算模組乘上時薪 × 倍率算出當日薪資成本。

---

## 3. Scope

### 3.1 In scope (本次 MVP，單一模組)

- 純函式 `classify_hours(input: LaborInput) -> LaborBuckets`
- Pydantic v2 輸入 / 輸出模型
- 全程 `decimal.Decimal`（小時數）+ `datetime`（時間）
- 8 / 10 / 12 小時切分（勞基法 4 桶）
- 國定假日整日 2.0x（無 tier 切分）
- 跨午夜分日處理（拆兩段，各自分桶）
- 12 小時硬上限：超過拋 `LaborLawViolationError`
- 時區處理：要求 tz-aware datetime；naive 拋例外
- ≥ 12 條 acceptance criteria

### 3.2 Out of scope

- 月薪 / 時薪 × 倍率（薪資計算另一模組）
- 排班 vs 打卡比對（差異分析另一模組）
- 跨多日打卡（單筆 clock_in / clock_out，本模組不處理「忘記打卡」自動補卡）
- 休息時間扣除（午休 1 小時等）— 由 caller 在傳入 clock_in/out 前先扣
- 月度工時上限 (54 小時/月) — 月聚合層處理
- 自動辨識「平日 / 例假日 / 休息日」差別 — 本模組只區分「國定假日 vs 非國定假日」
- DB 查詢 (`time_clocks`)、寫入 (`time_clocks.regular_hours` 等欄位)

---

## 4. Inputs — Pydantic schemas

下列是**錨定形狀**。

```python
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class LaborInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    employee_id: str                        # uuid str — echoed only
    clock_in: datetime                      # tz-aware REQUIRED (raises if naive)
    clock_out: datetime                     # tz-aware REQUIRED; > clock_in
    public_holidays: list[date]             # 國定假日 list, in `tz`'s calendar
    tz: str = "Asia/Taipei"                 # IANA tz; used for date(clock_in/out) calculation
```

### Validation rules (Pydantic field validators)

| Field | Rule |
|---|---|
| `clock_in`, `clock_out` | both MUST be tz-aware (`tzinfo is not None`); reject naive with `ValidationError` |
| `clock_out > clock_in` | strict greater-than; zero-duration or reversed → `ValidationError` |
| `public_holidays` | list MAY be empty |
| `tz` | must be a valid IANA tz string parseable by `zoneinfo.ZoneInfo`; otherwise `ValidationError` |
| `total_hours > 12` | NOT a Pydantic validation; raised by `classify_hours` as `LaborLawViolationError` (12h covers regular+OT1+OT2 max) |
| All `Decimal` outputs | `strict=True` not relevant on output; values quantized to 2dp |

> **Note**：勞基法 12 小時硬上限是針對「非假日的單日總工時」。若整段都落在國定假日，holiday bucket 可以 > 12（雖然實務上極少），本模組對 holiday-only 段**不**套 12h ceiling，因為 holiday 不是 OT tier 結構，超時責任在排班端。MVP 取簡化規則：**只有當「最終非 holiday 桶 (regular+OT1+OT2) 在任一 calendar day 上超過 12h」才拋例外**。詳見 §7。

---

## 5. Output schema

```python
class LaborBuckets(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str                        # echo
    regular_hours: Decimal                  # >= 0
    overtime_tier1_hours: Decimal           # >= 0 (1.34x band, max 2.00 per calendar day)
    overtime_tier2_hours: Decimal           # >= 0 (1.67x band, max 2.00 per calendar day)
    holiday_hours: Decimal                  # >= 0
    # invariant: sum(all four) == total_worked_hours, all to 2dp
```

### Rounding / quantization

- 所有 hour 欄位 `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`（小時 2dp，對齊 `time_clocks` 的 `Numeric(6,2)`）。
- 內部 timedelta 轉小時時用 `Decimal(seconds) / Decimal(3600)`，**不**透過 float。
- 加總後再 quantize；不要在每段 quantize 後相加（避免累積誤差）。

---

## 6. Public interface

```python
class LaborLawViolationError(ValueError):
    """Raised when a single calendar day's non-holiday hours exceed 12."""


def classify_hours(input: LaborInput) -> LaborBuckets:
    """Classify a clock-in/out interval into 勞基法 hour buckets.

    Pure function. No I/O, no global state, no logging side-effects.
    Raises:
        pydantic.ValidationError: malformed input (naive datetime, reversed range, bad tz).
        LaborLawViolationError: any single calendar day's regular+OT1+OT2 > 12h.
    """
```

模組必須以這個函式為唯一公開 API。其他 helper 全部以 `_` 開頭視為私有。

---

## 7. Classification rules (the contract)

### 7.1 Date splitting (cross-midnight)

1. 把 `clock_in` 與 `clock_out` 轉換到 `tz`（用 `zoneinfo.ZoneInfo(input.tz)`）。
2. 取得 `date_in = clock_in_local.date()`、`date_out = clock_out_local.date()`。
3. 若 `date_in == date_out`：單一段，直接分桶。
4. 若 `date_in != date_out`：拆兩段（**只支援跨一日午夜**；若 `clock_out - clock_in > 24h` 拋 `ValidationError`）：
   - 段 A: `[clock_in, midnight_of_date_out_in_tz)`，歸 `date_in`
   - 段 B: `[midnight_of_date_out_in_tz, clock_out]`，歸 `date_out`

### 7.2 Per-segment classification

對每一段，先看「該段對應的 calendar date 是否在 `public_holidays`」：

- **若 in public_holidays**：整段小時 → `holiday_hours` 桶（2.0x）。
- **若 not in public_holidays**：套用 8/10/12 切分。但**累積基準是「該 calendar day 內所有非 holiday 段的累積工時」**——因為跨午夜時，day A 的段已經吃掉 8 小時的話，新的一段（仍是 day A 範疇下沒有；新一段已歸 day B）從 0 開始算。
- 簡化：因為單筆 input 至多跨一日午夜，每個 calendar day 最多只有 1 個非 holiday 段，所以「per-segment」可以等同「per-day」處理：每段獨立用該段時數 H 套 8/10/12。

對該段時數 H（Decimal hours）：
- `regular = min(H, 8)`
- `ot1 = min(max(H - 8, 0), 2)` （第 9–10 小時）
- `ot2 = min(max(H - 10, 0), 2)` （第 11–12 小時）
- 若 `H > 12`：拋 `LaborLawViolationError`，訊息含該段對應 calendar date 與 H 值。

### 7.3 Aggregation

- 段 A、段 B 的桶相加，分別 quantize 至 2dp，組成 `LaborBuckets`。
- `holiday_hours` 不參與 12h ceiling 檢查（見 §4 註）。

### 7.4 What "calendar day" means

`public_holidays` 與 `date_in / date_out` 都是 `tz`（預設 Asia/Taipei）的 local date。**不**做 UTC 推算。日界線一律以 `tz` 為準。

---

## 8. Acceptance criteria

> 每一條都必須有對應的 pytest test case。所有 datetime 用 `datetime(..., tzinfo=ZoneInfo("Asia/Taipei"))` 構造。

| # | 名稱 | 描述 / 驗收條件 |
|---|---|---|
| AC-1 | 8h regular | clock_in=2024-03-15 09:00 TPE、clock_out=2024-03-15 17:00 TPE → regular=`Decimal("8.00")`、ot1=0、ot2=0、holiday=0。 |
| AC-2 | 9h = 8 reg + 1 OT1 | 09:00–18:00（無午休扣除）= 9h → regular=8.00、ot1=1.00、ot2=0、holiday=0。 |
| AC-3 | Exactly 10h | 09:00–19:00 = 10h → regular=8.00、ot1=2.00、ot2=0、holiday=0。 |
| AC-4 | 11h = 8 + 2 + 1 | 09:00–20:00 = 11h → regular=8.00、ot1=2.00、ot2=1.00。 |
| AC-5 | Exactly 12h | 09:00–21:00 = 12h → regular=8.00、ot1=2.00、ot2=2.00。**不**拋例外（12 是邊界 inclusive）。 |
| AC-6 | >12h raises | 09:00–22:00 = 13h，date 不在 public_holidays → 拋 `LaborLawViolationError`。 |
| AC-7 | Holiday all-day | clock_in=2024-02-28 (228 紀念日) 10:00 TPE、clock_out=同日 22:00、holidays=[2024-02-28] → holiday=`Decimal("12.00")`、regular=0、ot1=0、ot2=0。**不**拋例外（holiday bucket 不受 12h 上限約束）。 |
| AC-8 | Holiday >12h still ok | holiday 整段 13h → holiday=`Decimal("13.00")`、其他=0。**不**拋例外。 |
| AC-9 | Midnight crossing, both weekdays | 2024-03-15 22:00 → 2024-03-16 02:00 TPE，兩日皆非假日 → 段 A=2h (date 2024-03-15)、段 B=2h (2024-03-16)；regular=4.00 (=2+2)、其餘=0。 |
| AC-10 | Midnight crossing into holiday | 2024-02-27 22:00 → 2024-02-28 04:00 TPE、holidays=[2024-02-28] → 段 A (27日，非假日) 2h regular、段 B (28日，假日) 4h holiday → regular=2.00、holiday=4.00、其餘=0。 |
| AC-11 | Midnight crossing long shift | 2024-03-15 14:00 → 2024-03-16 04:00 (14h 跨日) → 段 A=10h (2024-03-15)、段 B=4h (2024-03-16)；段 A 分桶: reg=8、ot1=2；段 B: reg=4；總 regular=12.00、ot1=2.00、其餘=0。**不**拋例外（兩段各自 <=12h）。 |
| AC-12 | Single-day 13h crossing trigger | 一日內 13h (例如 09:00–22:00) 拋 `LaborLawViolationError`；但跨午夜 13h 拆成 10h + 3h 不拋（AC-11 變體）。文件化此差異。 |
| AC-13 | Naive datetime rejected | `LaborInput(clock_in=datetime(2024,3,15,9,0), ...)`（naive，無 tzinfo）→ `ValidationError`。 |
| AC-14 | Reversed range rejected | clock_in > clock_out → `ValidationError`。 |
| AC-15 | Zero duration rejected | clock_in == clock_out → `ValidationError`（嚴格 `>`）。 |
| AC-16 | Invalid tz rejected | `tz="Mars/Olympus_Mons"` → `ValidationError`（`ZoneInfo` 拋 `ZoneInfoNotFoundError`，包成 `ValidationError`）。 |
| AC-17 | Decimal precision (fractional hours) | clock_in=09:00、clock_out=17:30 = 8.5h → regular=8.00、ot1=`Decimal("0.50")`。**不**用 float 中介。 |
| AC-18 | >24h crossing rejected | clock_out - clock_in > 24h → `ValidationError`（本模組僅支援跨一日午夜）。 |
| AC-19 | Total hours invariant | `regular + ot1 + ot2 + holiday == round(total_seconds/3600, 2)`（2dp）。 |

---

## 9. Edge cases (must be enumerated in tests)

- **跨午夜長班**：見 AC-11、AC-12。「12h 上限」是**單一 calendar day**上的限制，跨日兩段各自獨立 ≤ 12h。
- **整段在假日**：見 AC-7、AC-8。holiday bucket 不切 tier、不受 12h ceiling 約束（勞基法 §39 對假日加班倍率單一）。
- **跨日進入假日**：見 AC-10。段 A 按平日切分，段 B 整段歸 holiday。
- **DST / 跨 tz / >24h shift**：MVP 鎖定 `Asia/Taipei`（無 DST）；clock_in/out 在不同 tz 由 Pydantic 自動轉到 `input.tz`；>24h 拒絕（AC-18）。
- **時段精度**：跨午夜邊界點歸**段 B**（half-open `[midnight, ...]`），避免重複計算。
- **空 `public_holidays`**：合法；所有日子按平日切分。

---

## 10. Constraints (hard requirements)

- **檔案結構**：
  - 模組：`labor_hours_classifier.py`（單一檔案）
  - 測試：`test_labor_hours_classifier.py`（單一檔案，pytest）
- **依賴**：Python 3.12 標準庫（`datetime`、`zoneinfo`、`decimal`）+ `pydantic>=2.5`。**禁止** pandas、numpy、pytz、arrow、其他第三方。
- **數值型別**：所有小時數一律 `decimal.Decimal`；不允許 `float`（包含 `total_seconds() / 3600` 必須走 Decimal）。
- **純函式**：`classify_hours` 不得有 I/O、不得讀寫全域狀態、不得 logging。
- **型別標註**：每個公開 / 私有函式都要有完整 type hints。
- **不可變**：所有 Pydantic 模型 `model_config = ConfigDict(frozen=True, strict=True)`（output 用 `frozen=True` 即可）。
- **錯誤處理**：輸入驗證錯誤透過 Pydantic 自動拋 `ValidationError`；勞基法 violation 拋自訂 `LaborLawViolationError`（繼承 `ValueError`）。
- **無 magic numbers**：以下必須是 module-level 常數：
  - `REGULAR_HOURS_CAP = Decimal("8")`
  - `OVERTIME_TIER1_CAP = Decimal("2")` (上限 hours added in tier 1)
  - `OVERTIME_TIER2_CAP = Decimal("2")` (上限 hours added in tier 2)
  - `DAILY_HOURS_HARD_CEILING = Decimal("12")`
  - `DEFAULT_TZ = "Asia/Taipei"`
- **No `eval` / `exec`**。

---

## 11. Out of scope (重申，避免 Coder drift)

- 時薪 × 倍率計算（薪資模組）
- 休息時間扣除（caller 預處理）
- 月度工時上限 / 月聚合
- 平日 / 例假日 / 休息日的差別處理（MVP 二元分類：holiday vs not）
- 排班比對 (`shifts` 表)
- DB 查詢 / 寫入 `time_clocks`
- 「忘記打卡」自動補卡
- 多日 / >24h 工時
- DST 處理

---

## 12. Connection to the broader system

本模組輸出對應 `restaurant_api/models/hr.py` 中 `TimeClock` ORM 的四個欄位（命名完全對齊）：

| 本模組輸出欄位 | `time_clocks` 欄位 |
|---|---|
| `regular_hours` | `regular_hours` `Numeric(6,2)` |
| `overtime_tier1_hours` | `overtime_tier1_hours` `Numeric(6,2)` |
| `overtime_tier2_hours` | `overtime_tier2_hours` `Numeric(6,2)` |
| `holiday_hours` | `holiday_hours` `Numeric(6,2)` |

`mv_daily_pnl` 的 labor_cost CTE 直接用這 4 個欄位 × 倍率 (1.0 / 1.34 / 1.67 / 2.0) × hourly_rate 算出 labor_cost：

```sql
COALESCE(tc.hours_regular,0)
+ COALESCE(tc.hours_overtime_1,0)*1.34
+ COALESCE(tc.hours_overtime_2,0)*1.67
+ COALESCE(tc.hours_holiday,0)*2.0
```

（docs/04 §7 的 labor_cost CTE；ORM 欄位名以 `restaurant_api/models/hr.py` 為準，舊文件中的 `hours_*` 命名後續會對齊。）

呼叫端流程（不在本模組）：

1. 員工 clock_out 時，POS / 打卡 app 取得 `(clock_in_at, clock_out_at)`
2. 查當月 `public_holidays`（內建表或第三方 API）
3. 組 `LaborInput`、呼叫 `classify_hours`
4. 把四桶寫入 `time_clocks` 對應欄位
5. 月薪 / 時薪結算時把四桶 × 倍率 × hourly_rate 加總

---

## 13. Done = all of:

1. `labor_hours_classifier.py` exists, type-checks cleanly, no unused imports.
2. `test_labor_hours_classifier.py` 包含 AC-1 ~ AC-19 對應 test functions（命名 `test_ac_01_*`、`test_ac_02_*` …）。
3. `pytest test_labor_hours_classifier.py` 全綠。
4. 沒有 float 出現在模組或測試的任何地方（小時數一律 `Decimal`）。
5. `classify_hours` 的 docstring 含一段最小 usage 範例與四桶倍率對照表。

---

## 14. 給 PM Agent 的提醒

- **倍率不在本模組**：brief 裡的 1.34x / 1.67x / 2.0x 只是描述；本模組只回桶數，乘倍率是 caller 的責任。Coder 若把倍率寫進來會跟薪資模組重複，月底結算對不上。
- **欄位命名對齊 ORM**：直接用 `restaurant_api/models/hr.py` 的 `TimeClock` 欄位名，不要縮寫成 `ot1` / `ot2`。
- **跨午夜 vs 12h 上限**：AC-11 / AC-12 是最容易誤實作的點。Coder 直覺會寫「總時數 > 12 就拋」，但跨午夜長班兩段獨立各 ≤ 12h，PR review 必看。
- **holiday-only 段不切 tier、不受 12h 上限**：勞基法 §39 對假日加班倍率單一；違法的「假日上 16 小時」由排班 / 同意書層處理，不在本模組範疇。
- **public_holidays 由 caller 提供**：本模組不內建假日表；caller 建議用內政部公告的「政府行政機關辦公日曆表」。

— end of brief —
