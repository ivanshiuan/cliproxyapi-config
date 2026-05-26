# 任務簡報 — 台灣統一編號驗證模組

## 背景

台灣的「統一編號」(Uniform Invoice Number, 統編) 是 8 位數字，作為公司法人的稅籍識別。所有 B2B 發票（三聯式 / 公司開立）都會帶這個欄位。

在我們的餐飲系統中，`orders.buyer_tax_id` 欄位儲存買方統編；當顧客要求開立公司發票時，POS 必須**在送出前**驗證統編格式正確 — 統編填錯導致發票報廢的損失，財政部不會幫你補。

財政部公告的驗證演算法（2023 版）：

> 統編採 modified Luhn / Mod-10 變形。對 8 位數字 d₁d₂d₃d₄d₅d₆d₇d₈，逐位乘以權重 [1, 2, 1, 2, 1, 2, 4, 1]：
> 1. 若乘積為兩位數，將兩位數字相加（例 8 × 2 = 16 → 1 + 6 = 7）
> 2. 將 8 個調整後的數字加總，得到 S
> 3. 若 d₇ = 7（中間第七位是 7），則 S' = S + 1 也視為合法（特殊規則）
> 4. 若 S（或 S'）能被 5 整除（即 S mod 5 == 0），統編合法；否則不合法

## 目標

純 Python 函式：給定字串輸入，回傳是否合法 + 結構化驗證結果。零 I/O、純函式、可在 POS 送出訂單前低延遲呼叫（< 0.1ms）。

## 範圍

### In scope

- 正規化輸入：去除空白、破折號、全形數字轉半形
- 驗證長度（必須剛好 8 位數字）
- 驗證合法數字（0-9）
- 計算校驗碼並比對
- 套用「第七位為 7」的特殊規則
- Pydantic input/output 模型
- ≥ 12 個 acceptance criteria

### Out of scope（後續再做）

- 即時查詢財政部 API 驗證該統編是否「真實註冊」（這需要連網，本模組純離線）
- 統一發票字軌號碼驗證
- 載具號碼驗證（手機條碼 / 自然人憑證）— 不同演算法

## 公開介面

```python
from decimal import Decimal  # not needed here, but enforce stdlib-only outside Pydantic

class TaxIdValidationInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    raw: str  # user input as-typed

class TaxIdValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    is_valid: bool
    normalized: str | None  # 8-digit canonical form, or None if structure invalid
    reason: str             # explanation: "ok" | "wrong_length" | "non_numeric" | "checksum_failed"

def validate_uniform_invoice(input: TaxIdValidationInput) -> TaxIdValidationResult:
    """Validate a Taiwan uniform invoice number (統一編號)."""
```

## Acceptance Criteria

每個 AC 需有對應 pytest 測試。

1. **AC-1 happy path**: `validate_uniform_invoice(TaxIdValidationInput(raw="12345675"))` → `is_valid=True`, `normalized="12345675"`, `reason="ok"`（這是個合法的測試值）

2. **AC-2 known-good**: 台積電公司統編 `22099131` → `is_valid=True`

3. **AC-3 known-good (with 7-rule)**: 中華電信統編 `96979933`（第七位是 3，不觸發 7-rule）→ `is_valid=True`

4. **AC-4 7-rule case**: 構造一個 d₇=7 且原本 S mod 5 != 0、但 S+1 mod 5 == 0 的統編，驗證 `is_valid=True`

5. **AC-5 invalid checksum**: `12345678` → `is_valid=False`, `reason="checksum_failed"`

6. **AC-6 wrong length (short)**: `1234567` → `is_valid=False`, `normalized=None`, `reason="wrong_length"`

7. **AC-7 wrong length (long)**: `123456789` → `is_valid=False`, `normalized=None`, `reason="wrong_length"`

8. **AC-8 non-numeric**: `1234567A` → `is_valid=False`, `reason="non_numeric"`

9. **AC-9 normalization (whitespace)**: `" 22099131 "` → `is_valid=True`, `normalized="22099131"`

10. **AC-10 normalization (dashes)**: `"220-99131"` 或 `"22-09-9131"` → `is_valid=True`, `normalized="22099131"`

11. **AC-11 normalization (全形數字)**: `"２２０９９１３１"`（全形）→ `is_valid=True`, `normalized="22099131"`

12. **AC-12 empty string**: `""` → `is_valid=False`, `reason="wrong_length"`

13. **AC-13 None safety**: `raw` 設成空字串時不應 raise；Pydantic `strict=True` 已拒絕 `None`

14. **AC-14 performance**: 單次呼叫應在 < 0.1ms 完成（用 `pytest-benchmark` 不必，純斷言 `time.perf_counter()` 差值即可，loop 1000 次取平均）

15. **AC-15 immutability**: `TaxIdValidationInput(raw="x").raw = "y"` 應 raise `ValidationError`（frozen=True 確保）

## Edge cases to enumerate

- 全形 ASCII 數字（半形數字也要支援，雙形交錯如 `"22０99131"`）
- 各種破折號變體：`-`、`–`（en-dash）、`—`（em-dash）；只處理 `-` 即可
- 多個連續空白
- 大小寫無意義（統編只含數字）

## 硬性約束

- 單檔模組 `uniform_invoice_validator.py`
- 單檔測試 `test_uniform_invoice_validator.py`
- Python 3.12 stdlib + `pydantic>=2.5` only
- 純函式，無 I/O、無全域狀態、無快取
- 全形數字轉換用 `unicodedata.normalize("NFKC", s)` 或手動 mapping（前者更穩）
- 不允許 `eval`、`exec`、第三方庫除了 Pydantic

## 不要做

- 不要查詢財政部 API
- 不要做 GUI / CLI
- 不要做歷史記錄
- 不要做警告日誌（純函式，呼叫者自己處理）

## 連結後續系統

完成後將被 `restaurant_api/` 在以下地點呼叫：

1. **POS 開單流程**：當顧客選擇開立公司發票時，前端送回 `buyer_tax_id`；POS service 在寫入 DB 前用本模組驗證。
2. **訂單匯入腳本**：批次匯入外部 POS 歷史訂單時做資料清洗。

`Order.buyer_tax_id` 欄位定義見 `restaurant_api/models/orders.py`。

## 給 PM Agent 的提醒

- 上方 AC-1 到 AC-15 已經是 testable 形式，PM 不需要再延伸太多
- Architect 應該注意「checksum 演算法錯一位整個系統壞掉」，要求 Coder 寫 1-2 個 property-based 不必（pytest 內就好），但要堅持每一條財政部公告的規則都有對應測試
- Coder 必須用 `unicodedata.normalize` 處理全形，不要自己手刻 mapping table（容易漏字元）
