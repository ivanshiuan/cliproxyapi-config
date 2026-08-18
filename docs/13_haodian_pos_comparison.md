# 13 — 好點 POS 完整功能整合 × 全域餐飲管理系統對照

> 來源：好點 POS 官方功能介紹（2026.08 版，Ivan 上傳）。
> Demo（後台 54.238.162.241:9081、前台 posmobile-qkb923ds.manus.space）因本環境網路白名單無法實測，本文以官方功能文件為準。
> 姊妹篇：`docs/12_hr_surpass_plan.md`（Aibou Crew HR 軸）、`docs/knowledge/2026-07-23-aibou-crew-competitor.md`。

---

## A. 好點 POS 功能全整合（他們有什麼）

**定位**：餐飲店家一站式營運系統 — 現場點餐→收銀→廚房出單→線上點餐→外送整合→會員→打卡→報表。目標客群：早餐店、便當店、飲料店、小吃店、餐廳、咖啡廳、攤商。

| 領域 | 功能明細 |
|---|---|
| 前台點餐 | 內用/外帶、桌號、分類快找、購物車、數量調整、單筆/商品備註 |
| 商品客製 | 客製群組（單選/複選/必選/選擇數量）；甜度冰量加料熟度醬料套餐 |
| 送單/未結單 | 先送單不結帳、未結單保留、叫出加點、先點後結 |
| 結帳 | 點餐畫面直入結帳、多支付方式紀錄、錢箱連動 |
| 訂單管理 | 多條件查詢（編號/日期/狀態/來源/取餐方式/支付）、明細、加點、補印、退款、合併 |
| 出單列印 | 櫃台+廚房出單機、網路連線、補印、標籤機（杯貼/餐盒） |
| 線上點餐 | QR 掃碼點餐、LINE 點餐入口、線上營業時間設定 |
| 外送整合 | 外送平台串接、線上單集中看、來源/取餐型態/預計時間/金額辨識 |
| 支付/發票 | 多元支付掃碼盒、對帳、電子發票開立+發票機+載具（指定方案） |
| 會員 | 顧客資料、熟客管理（模糊帶過 — 依方案） |
| 員工 | 員工資料+編號、簽到退打卡、出勤報表、停用保留歷史 |
| 桌位 | 桌號建立、區域命名、前台顯示控制 |
| 菜單後台 | 分類 CRUD+排序、商品 CRUD+售價、客製群組重用+批次連結、停售/隱藏、**通路價格**、預覽後發布 |
| 報表 | 日報（營收/支付/明細）、月報、商品銷量/銷售額分析、**指定時段營收**（午晚餐/班別/活動）、對帳管理（未結+退款+各支付通路） |
| 硬體 | POS 主機、出單機、錢箱、掃碼盒、發票機、標籤機 |

**他們的本質**：一台好用的「收銀+出單+接單」機器，資料只到「營收」層 — **沒有成本、沒有損益**。

---

## B. 對照總表（✅ 我們領先 / 🟡 部分 / ❌ 我們缺）

### B1. 我們領先的（他們沒有或很弱）

| 領域 | 我們有 | 他們 |
|---|---|---|
| **真損益** | mv_daily_pnl：營收−COGS−人力−廢棄=淨利 | ❌ 只有營收報表 |
| **庫存/BOM/COGS** | Ingredient/Recipe/StockMovement（append-only）、進貨、盤點調整、COGS 變異 | ❌ 完全沒有 |
| **成本事件** | 廢棄/員工餐/試菜 (`/events`) | ❌ 沒有 |
| **會員深度** | 集點 ledger、儲值（top-up/spend/ledger）、轉介、RFM、streak、分級 | 🟡 「會員系統依方案」一句話 |
| **行銷活動** | 輪盤活動、獎項、票券發放/核銷、海報+QR 產生、活動統計 | ❌ 沒有 |
| **KDS** | kitchen queue + 逐 line 狀態機（queued→cooking→ready→served） | 🟡 只有「出單機列印」— 印出來就斷線，無狀態追蹤 |
| **訂位/排隊** | Reservation + WalkInQueue 狀態機 | ❌ 沒有 |
| **勞基法工時** | 四桶分級（1.34/1.67/2.0）、12h 上限、跨午夜、國定假日表 | 🟡 只記上下班時間 |
| **稽核** | audit_log DB 層 append-only | ❌ 未宣稱 |
| **UGC/LINE** | UGC 審核流、LINE webhook、會員分眾廣播 | 🟡 LINE 只當點餐入口 |
| 發票**欄位深度** | invoice_status 全生命週期（開立/作廢/折讓/中獎/兌獎）、載具、統編 | 🟡 「依方案開立」 |

### B2. 我們缺的（他們有、我們要補）— 本次補足範圍

| # | 缺口 | 現況 | 補足方案 |
|---|---|---|---|
| G1 | **菜單後台管理 API** | MenuCategory/MenuItem model 在、**無 router** | 新 `menu_admin` router：分類 CRUD+排序、商品 CRUD、停售/隱藏 |
| G2 | **商品客製群組** | **無 model** — 甜度/冰量/加料做不了 | 新 model：ModifierGroup/ModifierOption + 商品連結；點餐時 line 帶客製快照 |
| G3 | **桌位管理** | **無 Table model**；Order 無桌號 | 新 model DiningTable + `tables` router；Order 加 `table_id` |
| G4 | **內用/外帶/來源** | Order 無 service_type/order_source | Order 加欄位：`service_type`(dine_in/takeout/delivery)、`order_source`(pos/qr/line/ubereats/foodpanda) |
| G5 | **加點**（未結單再點） | 只有 create/get/close/void | `POST /orders/{id}/lines` — open 單加 lines、進 KDS |
| G6 | **退款** | REFUNDED 在 enum、無 endpoint | `POST /orders/{id}/refund` — closed→refunded、記原因、audit |
| G7 | **未結單/多條件查詢** | 無列表端點 | `GET /orders` — status/date/source/service_type/payment 過濾 |
| G8 | **報表 API** | mv_daily_pnl 只在 DB | 新 `reports` router：日報、月報、商品分析、時段營收、對帳 — **每張都比他們多「成本+毛利」欄** |

### B3. 缺但本次不補（另開 spec / Phase 2+）

| 缺口 | 理由與去處 |
|---|---|
| QR 顧客自助點餐公開端點 | 需 auth/防濫用設計；G1–G5 是它的地基。另開 `public_ordering` spec |
| 外送平台串接（UberEats/foodpanda webhook） | 需平台商家帳號與審核；`order_source` 欄位本次先留好 |
| 電子發票實際開立（加值中心 API） | 欄位已齊、`specs/uniform_invoice_validator.md` 已有；需財政部/加值中心憑證 |
| 出單機/標籤機/錢箱硬體 | ESC/POS 驅動屬邊緣端；KDS 已取代廚房紙單 |
| 通路價格（同品不同通路不同價） | 等外送串接一起做，避免空轉 |
| 合併訂單 | 使用頻率低、帳務複雜（發票/支付拆分），Phase 2 |
| 線上營業時間 | 跟 public_ordering 一起 |

---

## C. 一句話定位（對打好點 POS）

> 好點 POS 告訴你今天**收了多少錢**；我們告訴你今天**賺了多少錢** — 同樣會點餐、出單、對帳，但每一筆訂單都連著食材成本、人力成本和會員終身價值。

---

## D. 本次補足工程（G1–G8 落地）

1. **Models + migration**：ModifierGroup/ModifierOption/MenuItemModifier、DiningTable、Order.table_id/service_type/order_source、OrderLine.modifiers(JSONB 快照)
2. **`menu_admin` router**：`/menu/categories` CRUD、`/menu/items` CRUD+停售、`/menu/modifier-groups` CRUD+連結商品
3. **`tables` router**：桌位 CRUD、啟用/停用
4. **orders 擴充**：加點、退款、列表查詢
5. **`reports` router**：`/reports/daily`、`/reports/monthly`、`/reports/products`、`/reports/period`、`/reports/reconciliation`
6. 全部走既有 conventions：Decimal、UUIDv7、tenant_id、DomainError、audit_service、AsyncClient 測試

— end —
