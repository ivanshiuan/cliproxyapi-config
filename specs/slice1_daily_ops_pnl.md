# 切片簡報：每日營運登錄 → 即時真實損益（切片 1 / 楔形）

> **切片名稱：** `slice1_daily_ops_pnl`
> **產品藍圖：** `docs/05_zhendian_product_blueprint.md` Part 2.2（楔形）、Part 7（切片路線圖）
> **服務的 JTBD：** J1「今天到底賺不賺、錢去哪了，我要一眼看懂，不要等會計。」
> **狀態：** 已規劃，可直接施工（或拆成 DevSwarm 子任務）
> **與單模組 spec 的差異：** 這是一份「**垂直切片簡報**」，比 `specs/profit_calc.md`
> 這種純函數 brief 大一階；它內部把可施工單元 U1–U6 拆清，每個單元都能各自施工/丟蜂群。

---

## 1. 目標

店長每天 **~90 秒**登錄「當日營收 / 成本 / 工時」→ 系統當場算出：
1. **今日真實損益**（real P&L，含被吃掉的隱形成本）
2. **本月累計**（month-to-date 真實淨利）
3. **90 天現金流預測**

這條垂直線從 **HTTP 端點 → service → 資料庫 → 純運算 → 真實數字**全程打通，
能在 BUFF HOTPOT 自家店每天真用。它是楔子：撐開後 CRM / 叫貨 / 排班順勢生長。

---

## 2. 完成定義（Definition of Done，照藍圖 Part 5.1，逐條驗收）

一個功能同時滿足全部才算「做完」，否則算「沒做」：

1. **資料貫通**：登錄的數字真的寫進 PostgreSQL（非畫面記憶體），重整還在、可被另一個 session 讀到。
2. **計算正確**：與真實單據「手算對帳」一致——驗收門檻是**連續 14 天誤差為零**（藍圖切片 1 完成標準）。
3. **可複盤**：端點回的每個數字都能往下鑽，看到它由哪幾筆來源列算出（drill-down to source rows）。
4. **錯誤有出口**：缺資料 / 重複登錄 / 日期未來 / 跨租戶，都回明確的 `DomainError`，不是 500。
5. **過三秒測試**：回應結構讓人三秒看懂「今天賺多少、錢去哪」（前端在後續切片，但 API 形狀先為它鋪路）。

---

## 3. 重用既有資產（明列路徑 — 禁止重造）

這條切片**約 80% 已就位**。施工的本質是「接線」，不是重寫運算。

### 3.1 損益運算引擎（核心，原封不動重用）
- `restaurant_api/services/calc/profit_calc.py`
  - 公開純函數：`compute_daily_pnl(input: DailyPnLInput) -> DailyPnLOutput`
  - 輸入模型 `DailyPnLInput`（`business_date`, `store_id`, `orders: list[OrderLine]`,
    `discounts: list[Discount]`, `cost_events: list[CostEvent]`,
    `platform_fees: list[PlatformFee]`, `fixed_costs: list[FixedCostLine]`, `labor_cost`）。
  - 子模型欄位（精確）：
    - `OrderLine(item_id, qty, unit_price, cogs_actual, cogs_theoretical)`
    - `Discount(order_id, kind∈{percent,amount,comp,allowance,employee}, value)`
    - `CostEvent(kind∈{waste,staff_meal,tasting}, cost)`
    - `FixedCostLine(name, daily_amount)`、`PlatformFee(source, amount)`
  - 輸出 `DailyPnLOutput`：`gross_revenue`, `discount_total`, `net_revenue`,
    `cogs_actual_total`, `cogs_theoretical_total`, `cost_waste`, `cost_staff_meal`,
    `cost_tasting`, `platform_fee_total`, `labor_cost`, `fixed_cost_total`,
    `gross_profit_real`, `net_profit_real`, `cogs_variance_pct`, `cogs_variance_flag`。
  - **鐵律**：aggregator 只負責「把 DB 列組成 `DailyPnLInput`」，**數學一律由此引擎算**，不得在別處重算。

### 3.2 單一真實來源（落點）
- `mv_daily_pnl` 物化視圖（`docs/04_data_schema.md` ~700 行）：每店每日一列。
  儀表板 / 損益表 / 現金流預測**都讀它**，杜絕「各算各的」（藍圖 Part 6.4）。
- 注意 `specs/profit_calc.md` §12 已列出引擎輸出 ↔ `mv_daily_pnl` 欄位對映；
  本切片需在 spec 內**確認兩者一致**（U2/U3 的一致性測，見 AC）。

### 3.3 來源表（aggregator 從這些表讀一天的原始列）
- 營收：`orders` / `order_lines` / `order_discounts` / `order_payments`（`models/orders.py`）
- 實際 COGS：`stock_movements`（`movement_type='sale_consume'`，`models/inventory.py`）
- 工時 → 人工成本：`time_clocks`（已分桶 regular/overtime/holiday）+ `employees`（wage）
- 成本事件：`cost_events`（waste / staff_meal / tasting，`models/cost_events.py`）
- 現金對帳：`cash_drawer_sessions`（`models/cash.py`，`variance`）

### 3.4 慣例（照抄、不要發明）
- Router 形狀照 `restaurant_api/routers/orders.py`；service 形狀照 `services/orders_service.py`。
- DI 與例外：`api/deps.py`（`get_db` commit-on-success、tenant/store scope）+ `api/errors.py`（`DomainError` 系列，**不要** raw `HTTPException`）。
- 稽核：寫紀錄一律走 `services/audit_service.audit()`，不要直接 INSERT `AuditLog`。
- 金錢一律 `Decimal`、money 欄位用 `Money = Numeric(14,4)`；時間 tz-aware（DB UTC、API 回 Asia/Taipei）。
- 測試：用 `tests/conftest.py` 的 `client`（AsyncClient + ASGITransport）+ savepoint fixture，scope 到 `seed_tenant`/`seed_store`。

---

## 4. 精確缺口（要建什麼 — 拆成可施工單元 U1–U6）

> 每個單元可獨立施工；U1/U3 是 router-track（照 `.claude/agents/router-implementer`），
> U6 是純函數可丟 DevSwarm（照 `specs/profit_calc.md` 體例）。

### U1 — 每日營運登錄捕捉（手動入口）
店長手填當日營收/成本/工時的登錄模型 + `POST` 端點。切片 1 先支援**手動**；
切片 2 POS 自動回填後，此手動入口退位為「補登/校正」用途。

**資料落點（兩案，spec 內須拍板，建議 A）：**
- **A（建議）新表 `daily_ops_log`**：每店每日一列（`tenant_id`, `store_id`, `business_date`,
  手填 `revenue_manual`, `labor_cost_manual`, 雜項成本…, `source∈{manual,pos}`, `created_at`）。
  理由：① 不污染交易帳本 ② 符合 ledger append-only 法則（手動登錄是另一層，不是訂單）
  ③ 切片 2 接 POS 後，`source` 一欄即可區分手動 vs 自動，遷移乾淨。
- **B 直接寫進既有交易表**：否決——會讓「手填的 1 筆營收」和「POS 真實訂單」混在 `orders`，
  破壞可複盤性與帳本純度。

**錯誤有出口**：同店同日重複登錄 → 回 `409 DomainError`（提供「改用校正」路徑）；
`business_date` 在未來 → `422`；跨租戶 → `404`（不洩漏存在性）。

### U2 — Aggregator service（hydrate → 算）
新 service：`services/daily_pnl_service.py`（暫名）。
給定 `(tenant_id, store_id, business_date)`：
1. 從 §3.3 來源表 + U1 的 `daily_ops_log` 撈一天原始列；
2. 組成 `DailyPnLInput`（純整合：型別轉換、分組、把 `time_clocks×employees` 折成 `labor_cost`）；
3. 呼叫 `compute_daily_pnl()`；
4. 回 `DailyPnLOutput`。
**不得**在此重算任何損益數學。本月累計 = 對該月已登錄日逐日呼叫後加總（或讀 `mv_daily_pnl` 聚合，二擇一，spec 內定；建議讀 `mv_daily_pnl` 以維持單一真實來源）。

### U3 — HTTP 端點
- `GET /daily/{store_id}/{date}` → 回 `{ today: DailyPnLOutput, month_to_date: {...}, cashflow_90d: [...] }`
- 可複盤：附 `?drill=cogs` 之類參數回來源列摘要（或另一個 `GET /daily/{store_id}/{date}/sources`）。
- 認證：切片 1 先用既有 passcode `require_admin`（`api/auth.py`）；
  **不**在本切片做 RBAC 多角色（藍圖 Part 6.3 的 RBAC 是後續切片）。spec 內標明 scope 由 `tenant_id`+`store_id` 把關。

### U4 — `fixed_costs` 來源
引擎吃 `fixed_costs: list[FixedCostLine]`，但目前**無來源**。
spec 內拍板（建議）：新增小設定表 `store_fixed_costs`（每店每項固定成本 + 日攤額 `daily_amount`），
aggregator 讀它組進 `DailyPnLInput.fixed_costs`。次選：config 常數（不利多店，否決）。

### U5 — `platform_fee` 來源
引擎吃 `platform_fees: list[PlatformFee]`，目前無專欄。
spec 內拍板（建議）：來自 `order_payments`（依 `method` 對映平台費率）或新增 `platform_fee` 欄；
切片 1 可先由 U1 手動登錄一筆「平台費合計」，切片 2 再自動化。

### U6 — 90 天現金流預測（純函數，可獨立成 DevSwarm 計算 spec）
新模組 `services/calc/cashflow_forecast.py`，公開純函數
`forecast_cashflow(history, fixed_costs, known_receivables, known_payables, horizon_days=90) -> list[CashPosition]`：
- 輸入：近 N 日 `mv_daily_pnl`（淨利趨勢）、固定成本、已知應收/應付、期初現金。
- 輸出：90 日逐日現金部位曲線（含最低點與觸零日警示）。
- 同 `profit_calc` 紀律：`Decimal`、`frozen` Pydantic、純函數、無 I/O、AC ≥ 10、拒 float。

---

## 5. Acceptance criteria（≥ 10）

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | 手動登錄貫通 | `POST` 一筆當日登錄 → 寫進 `daily_ops_log`；重整 / 另一 session `GET` 仍在（資料貫通）。 |
| AC-2 | 端點回真實損益 | `GET /daily/{store}/{date}` 回 `DailyPnLOutput`，數字 = `compute_daily_pnl()` 對同一天輸入的結果。 |
| AC-3 | 單一真實來源一致 | 端點回的 `net_revenue` / `net_profit_real` 與 `mv_daily_pnl` 同 (store, date) 列**逐欄相等**。 |
| AC-4 | 14 天手算對帳零誤差 | 用 14 天真實/seed 單據，逐日端點輸出與手算 P&L 誤差為 `Decimal("0.00")`。 |
| AC-5 | 本月累計正確 | `month_to_date.net_profit_real` = 該月已登錄各日 `net_profit_real` 之和。 |
| AC-6 | 可複盤下鑽 | drill 參數/端點回的來源列摘要，加總後等於對應彙總欄（例：cogs_actual 來源列總和 = `cogs_actual_total`）。 |
| AC-7 | COGS 變異旗標透傳 | 引擎 `cogs_variance_flag=True` 時端點如實回傳，不被吞掉。 |
| AC-8 | 重複登錄有出口 | 同店同日二次 `POST` → `409 DomainError`（非 500、非靜默覆寫）。 |
| AC-9 | 未來日期拒絕 | `business_date > today(Asia/Taipei)` → `422 DomainError`。 |
| AC-10 | 租戶隔離 | A 租戶查 B 租戶的 store/date → `404`，不洩漏存在性；查詢 scope 到 `tenant_id`+`store_id`。 |
| AC-11 | fixed_cost 入帳 | `store_fixed_costs` 有資料時，`fixed_cost_total` 反映其日攤額；無資料時為 `0.00`，端點不崩。 |
| AC-12 | 90 天現金流 | `cashflow_90d` 回 90 筆；觸零日（若有）被標記；空歷史時回明確訊號非例外。 |
| AC-13 | 金錢型別 | 所有金額欄位皆 `Decimal`、2dp；輸入端拒 float。 |
| AC-14 | 稽核留痕 | 每筆登錄經 `audit_service.audit()` 寫一筆 `audit_log`（action 如 `daily_ops.log.create`）。 |

---

## 6. Out of scope（明列 — 防範圍蔓延）

- **React 前端 / BUFF 設計系統**：切片 1 只交付 API 形狀，UI 在後續。
- **AI 總機**：藍圖 Part 3.3 的對話框是貫穿層，不在本切片。
- **POS 自動回填營收**：= 切片 2，本切片只做手動登錄入口。
- **RBAC 多角色登入**：本切片沿用既有 passcode；多角色是後續切片。
- **多店比較 / 加盟管控**：後續切片（J4）。
- **稅務 / 退款流程**：在發票模組與後續模組。

---

## 7. 驗證（怎麼端到端證明真能用）

1. `make full-check`（ruff + pyright + pytest + alembic-check + db-smoke）全綠。
2. 新整合測（`tests/routers/test_daily_pnl.py`）覆蓋 AC-1 ~ AC-14，用 `client` fixture + 真 PG。
3. 手算對帳：用 `make seed` + `make demo-flow` 灌一日資料，端點輸出 ↔ `mv_daily_pnl` ↔ 手算三方對齊（AC-3/AC-4）。
4. U6 純函數另有單元測（≥ 10 AC），不依賴 DB。

---

## 8. 切片之後（藍圖 Part 7 順序，僅點到不展開）

切片 2 POS 自動回填 → 切片 3 CRM + AI 行銷 → 切片 4 採購叫貨 → 切片 5+ 排班/多店/加盟/AI 推演。
每完成一條，把該切片能力接進 AI 總機。**這個月只出手切片 1**。

— end of brief —
