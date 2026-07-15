# 21 — 領域詞彙表（Domain Glossary）

> 這是 mattpocock/skills 說的「共享領域語言」（CONTEXT.md / domain-modeling）在本專案的落地。
> **目的**：人和 AI 講同一個名詞時指同一個東西。Agent 太囉嗦、名詞漂移，九成是因為沒有這份表。
> **規矩**：新概念進 code 之前先進這裡；發現同一概念有兩套叫法，以這裡為準改掉另一套。
> 完整 DDL 與設計理由見 `docs/04_data_schema.md`（909 行，這裡只放「講話用的定義」）。

---

## 結構核心

| 詞 | 定義 | 講話時注意 |
|---|---|---|
| **tenant** | 一個加盟主/品牌主體，所有業務表的最上層隔離鍵 | 「跨租戶」= 紅燈詞，任何查詢都必須帶 `tenant_id` |
| **store** | tenant 底下的一間實體店 | 報表、庫存、班表都以 store 為單位 |
| **ledger 表** | append-only 的流水帳：`stock_movements`、`audit_log`、`customer_points_ledger`、`customer_stored_value_ledger` | 只能 INSERT。「改一筆 ledger」這句話不存在，要沖銷就再 INSERT 一筆反向紀錄 |
| **soft delete** | 顧客面紀錄用 `deleted_at` 標記，不真刪 | 「刪掉」對顧客資料永遠指 soft delete |

## 錢與成本

| 詞 | 定義 | 講話時注意 |
|---|---|---|
| **Money** | `Numeric(14, 4)` 的 ORM 別名，所有金額欄位的唯一型別 | Python 端一律 `Decimal`；「用 float 算一下就好」不存在 |
| **COGS** | 銷貨成本（Cost of Goods Sold），由 BOM 展開 + 進貨成本算出 | 有 `cogs_variance_detector` spec 抓理論 vs 實際的差異 |
| **BOM / recipe** | 菜品的物料清單（`recipes` 表）：一份餐點消耗哪些 `ingredients` 各多少 | 「配方」「BOM」「recipe」是同一個東西，寫 code 用 recipe |
| **cost_event** | 非食材的成本事件（房租、修繕…），走 `cost_events` 表 | 跟 stock 進貨分開，不要混 |
| **mv_daily_pnl** | 每日損益的 materialized view | 「今天賺多少」的 SSOT，不要自己現算 |

## 營運物件

| 詞 | 定義 | 講話時注意 |
|---|---|---|
| **order / order_line** | 一張單 / 單上的一個品項行 | 折扣掛在 `order_discounts`，付款掛在 `order_payments`，不進 order 本體 |
| **stock_movement** | 庫存異動流水（進貨/耗用/報廢/盤點調整） | ledger，append-only；「庫存現量」是流水的加總，不是一個欄位 |
| **waste_event** | 報廢事件（過期、損耗） | 食安稽核會查，必留 audit |
| **time_clock / shift** | 打卡紀錄 / 排班 | 勞基法工時計算的輸入，見 `labor_hours_classifier` spec |
| **stored value / points** | 儲值金與點數，各自有 ledger | 兩者不可互轉（除非 spec 明說）；都是錢的近親，Decimal 伺候 |
| **voucher / spin / prize** | 行銷活動的券 / 抽獎 / 獎項（`campaign_*` 表） | 「轉盤活動」見 `docs/12_launch_wheel_campaign.md` |

## 台灣特有

| 詞 | 定義 | 講話時注意 |
|---|---|---|
| **統一發票** | 台灣財政部發票制度，有固定字軌+8 碼格式與驗證邏輯 | 驗證走 `uniform_invoice_validator` spec，不要自己 regex |
| **勞基法工時** | 加班費分段（平日 1.34/1.67、休息日、國定假日不同倍率） | `public_holidays` 表是輸入之一；算錯是法律問題不是 bug |
| **食安溯源** | 進貨批號→耗用→成品的可追溯鏈 | 動 stock/recipe 相關 schema 前先讀 `docs/08_safety_compliance.md` |

## 系統詞

| 詞 | 定義 | 講話時注意 |
|---|---|---|
| **DevSwarm** | LangGraph 4-agent 蜂群，吃 `specs/*.md` 產出單 module + 單 test | 「跑蜂群」= `/swarm`；一任務 < $5 |
| **RestSwarm** | FastAPI + 25 表 PG 的餐飲後端本體（`restaurant_api/`） | 跟 DevSwarm 是兩層，不要混稱 |
| **spec / task brief** | `specs/*.md`，蜂群的輸入合約，AC ≥ 10 | 「補 spec」= `/spec` 或 spec-writer agent |
| **promote** | 把蜂群產出從 `workspace/` 搬進正式 codebase | `make promote TASK=<id>` |

---

## 維護規則

1. 名詞有歧義 → 開 PR 改這份表，先對齊再寫 code。
2. `/grill` 訪談時發現使用者用了表上沒有的新名詞 → 訪談完順手登記。
3. `/arch-review` 會拿這份表當「同概念雙命名」的比對基準。
