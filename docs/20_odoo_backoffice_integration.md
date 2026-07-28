# 20 — Odoo 純後台整合規劃（會計 + 廠商帳 + Claude 對話層）

> **一句話**：讓 **restaurant_api 繼續當「營運真相」**，把 **Odoo 收窄成「財務後台」**（總帳、應付/廠商帳、供應商主檔、正規採購/報價文件），
> **Claude 坐在兩邊之上當對話層**——用嘴查、用嘴記、用嘴對帳。
> 兩套系統各守**單一真相來源（SSOT）**，靠一條**單向為主、回讀為輔**的資料流縫起來，絕不做「兩本帳」。
>
> 對應需求（Ivan 2026-07）：`把 Odoo 合理地變成純後台的會計跟廠商帳；餐飲系統優化到自動化；所有 data 整合進來`。
> 前置閱讀：`docs/04_data_schema.md §3`（ERP 表）、`docs/06_execution_plan.md`、`restaurant_api/integrations/line/`（整合範本）。

---

## 0. 為什麼是「純後台」而不是「換掉我們的系統」

那兩篇貼文的真正價值**不是 Odoo 這個軟體**，而是兩件事：

1. **正規進銷存流程模型**（採購單→進貨單→報價單→銷貨單→出貨單）——標準、不用自己發明。
2. **用嘴管系統**的 UX——老闆不學 ERP，只跟 AI 講話。

但 Odoo 是**通用 ERP**，它**不懂台灣餐飲**：不懂 BOM 扣料、不懂食安 `lot_no` 溯源、不懂勞基法工時分類、不懂統一發票字軌驗證、不懂你那條 append-only 的庫存 ledger。這些正是 `restaurant_api` 6,500 LOC 的核心資產。

所以結論很直接：

> **不要用 Odoo 取代 restaurant_api。也不要讓 Odoo 碰庫存數量。**
> **只把 restaurant_api 現在「刻意沒做深」的那一塊——財務會計 + 應付廠商帳——外包給 Odoo。**

`restaurant_api` 目前**完全沒有**的東西（Odoo 現成就有、且做得比我們自己刻好）：

| 缺口 | Odoo 模組 | 說明 |
|---|---|---|
| 總帳 / 日記帳（GL / Journal） | Accounting | 借貸分錄、試算表、損益表、資產負債表 |
| 應付帳款 / 廠商帳（AP） | Accounting / Purchase | 「這個月欠供應商 C 多少、何時到期、付了沒」 |
| 供應商主檔 + 付款條件 | Purchase / Contacts | `docs/04` 的 `suppliers` 表其實還沒落地 |
| 正規採購文件鏈 | Purchase | RFQ→PO→收貨→廠商發票→付款，狀態機完整 |
| 報價單 / 銷貨單（對 B2B 外燴/團購） | Sales | 若有企業客/外燴才需要 |

---

## 1. 鐵律：單一真相來源分工（本文最重要的一段）

**每一個「事實」只能有一個系統說了算。** 兩套系統若同時宣稱掌管同一筆數，遲早分岔，而你的錢與庫存最禁不起分岔。

| 事實類別 | SSOT | 另一邊的角色 |
|---|---|---|
| **庫存數量 / 進出異動**（`stock_movements`） | 🟢 **restaurant_api（唯一）** | Odoo **完全不存**庫存數量。它只收「金額分錄」。 |
| **食安溯源 `lot_no` / BOM 扣料** | 🟢 **restaurant_api（唯一）** | Odoo 無此概念，不碰。 |
| **勞基法工時 / 打卡 / 排班** | 🟢 **restaurant_api（唯一）** | Odoo 不碰（薪資分錄可事後推總數過去）。 |
| **POS 訂單 / 顧客點數** | 🟢 **restaurant_api（唯一）** | Odoo 只收「每日銷售總額」一筆分錄。 |
| **會計總帳 / 日記帳分錄** | 🔵 **Odoo（唯一）** | restaurant_api 只負責「推事件」，不自己記帳。 |
| **應付帳款 / 廠商欠款到期** | 🔵 **Odoo（唯一）** | restaurant_api 推「進貨→廠商發票」，付款狀態回讀。 |
| **供應商主檔** | 🔵 **Odoo（推薦當主）** | restaurant_api 存一份 `odoo_supplier_id` 對照即可。 |
| **統一發票（進項）驗證** | 🟢 restaurant_api 先驗 → 🔵 Odoo 記帳 | 兩者靠 invoice_number 勾稽（見 §6）。 |

**一句話記法**：**數量與作業 → restaurant_api；金額與帳務 → Odoo。**
庫存 ledger 是我們的心臟，**永遠不複製進 Odoo**；會計總帳是 Odoo 的心臟，**我們永遠不自己刻**。

---

## 2. 三層架構圖

```
                        ┌──────────────────────────────┐
   老闆 / 店長           │            Claude             │  ← 對話層（用嘴管）
   「A 何時到貨？」  ───▶ │   同時掛兩個 MCP，自動路由     │
   「欠供應商C多少？」    └───────┬───────────────┬──────┘
                                │(讀/寫)         │(讀/寫)
                    ┌───────────▼──────┐   ┌─────▼─────────────┐
                    │  restaurant_api  │   │       Odoo        │
                    │  ── 營運 SSOT ── │   │  ── 財務 SSOT ──  │
                    │ • POS / 訂單     │   │ • 總帳 / 日記帳   │
                    │ • 庫存 ledger    │   │ • 應付 / 廠商帳   │
                    │ • 食安 lot 溯源  │   │ • 供應商主檔      │
                    │ • 工時 / 排班    │   │ • 採購/報價文件   │
                    │ • 顧客 / 點數    │   │ • 財報            │
                    └───────┬──────────┘   └───────▲───────────┘
                            │                       │
                            │  事件推送（單向為主） │
                            └───────────────────────┘
                     進貨→廠商發票分錄 / 每日銷售分錄 /
                     報廢損失分錄 / 薪資總額分錄
                            （回讀：付款狀態、供應商主檔）
```

**兩條線分清楚**：

- **上層（Claude ↔ 兩系統）**：即時**讀**為主，偶爾**寫**（老闆下口頭指令）。走 MCP。
- **下層（restaurant_api → Odoo）**：**自動、批次、事件驅動**的財務事件推送。走 Odoo external API（XML-RPC / JSON-RPC）。

---

## 3. Claude 對話層：雙 MCP，自動路由

Claude 同時掛兩個 MCP server，依問題語意自己決定打哪邊——老闆不需要知道資料在哪：

| 老闆問句 | Claude 打哪 | 底層查詢 |
|---|---|---|
| 「產品 A 什麼時候到貨？」 | restaurant_api | 查 `purchase_orders.received_at` / pending PO |
| 「訂單 B 的料夠不夠出？」 | restaurant_api | BOM × 現有 `stock_movements` 結存 |
| 「這個月報廢多少錢？」 | restaurant_api | `stock_movements` type=waste × unit_cost |
| 「供應商 C 這個月下了幾張採購單？」 | 二擇一 | PO header 在誰身上就打誰（見 §5 決策） |
| 「這個月欠供應商 C 多少、何時到期？」 | **Odoo** | AP aging report |
| 「上個月損益表？毛利率多少？」 | **Odoo** | P&L 財報（分錄已同步進去） |
| 「幫供應商 C 開一張這批貨的採購單」 | 依主檔位置 | 見 §5 |

**MCP 從哪來**：

- **restaurant_api MCP**：**我們自己包一個**（FastAPI 既有端點外面套一層 MCP tool 定義，唯讀為主）。這是把「用嘴查營運」變成現實的關鍵，且完全在我們掌控。
- **Odoo MCP**：社群已有 Odoo MCP server（走 Odoo external API）。先接社群版驗證，之後可自包一個只開放白名單模型（`account.move`, `res.partner`, `purchase.order`…）的收斂版本。

> ⚠️ **鐵律**：Claude 對 Odoo 的**寫入**（開單、記帳）預設**要人確認**才執行——尤其動到錢的分錄。呼應 Windsor/一般 write-action 慣例：**先 dry-run 給看，老闆點頭才 commit。**

---

## 4. 下層自動同步：mirror `integrations/line/` 的成熟模式

**不要重新發明整合層。** `restaurant_api/integrations/line/messenger.py` 已經是教科書級範本，Odoo 整合**原封不動照抄**這個四件套：

```
restaurant_api/integrations/odoo/
├── __init__.py
├── client.py          # ABC 契約 + Stub + Http + get_odoo() DI singleton
└── postings.py        # 「業務事件 → Odoo 分錄」的轉譯（純函式，可測）
```

對照 LINE 的結構：

| LINE 範本（既有） | Odoo 對應（新增） | 職責 |
|---|---|---|
| `LineMessenger(ABC)` | `OdooClient(ABC)` | 窄介面：`create_vendor_bill()`, `post_journal_entry()`, `get_ap_aging()`, `upsert_supplier()` |
| `StubLineMessenger` | `StubOdooClient` | 記憶體實作，記錄所有呼叫，**所有測試都用它**（deterministic，零網路） |
| `HttpLineMessenger` | `HttpOdooClient` | 真實 Odoo external API（XML-RPC/JSON-RPC 或 REST），`from_env()` 讀 `ODOO_URL/DB/API_KEY` |
| `get_messenger()` | `get_odoo()` | env 有 `ODOO_API_KEY` → Http，否則 → Stub。**dev/測試自動走 Stub，不需要真 Odoo。** |
| `LineApiError` | `OdooApiError` | 非 2xx 包裝，call site log 得到可行動訊息 |

**同步觸發點**（新增一個 job，落在既有 `jobs/`）：

```
restaurant_api/jobs/odoo_sync.py     # APScheduler，夜間 03:45（避開 points_expire 03:30）
restaurant_api/services/odoo_sync_service.py   # 純 async 業務邏輯，無 HTTP
```

**推什麼分錄過去**（每一筆都是冪等：帶 `external_id` = restaurant_api 的 source uuid，Odoo 端 upsert，重跑不重複）：

| restaurant_api 事件 | → Odoo 分錄 | 頻率 |
|---|---|---|
| `POST /stock/purchases`（進貨） | **廠商發票（Vendor Bill）**：借 存貨/費用、貸 應付—供應商C | 事件即時 or 夜間批次 |
| 每日 POS 收班 | **銷售日記帳**：借 現金/應收、貸 營業收入、貸 銷項稅 | 每日一筆彙總（不逐單） |
| 報廢 / 員工餐 / 試菜（cost_events） | **損失/費用分錄**：借 報廢損失、貸 存貨 | 夜間彙總 |
| 薪資期結算（payroll） | **薪資費用分錄**：借 薪資費用、貸 應付薪資 | 每期一筆總額 |

> **鐵律**：推過去的是**金額彙總分錄**，不是逐筆營運資料。Odoo 不需要知道你賣了幾份雞排、用了哪批雞肉——它只需要「今天營收 X、進貨應付 Y」。**逐筆明細永遠留在 restaurant_api。**

**回讀**（Odoo → restaurant_api，唯讀快取，非 SSOT）：

- 廠商發票**付款狀態**（已付/未付/部分）→ 讓老闆用嘴問「付了沒」。
- 供應商主檔異動 → 同步 `odoo_supplier_id` 對照表。

---

## 5. 一個必須由指揮官拍板的決策：採購單（PO）header 放誰身上？

這是本規劃**唯一真正的岔路**，因為它決定「開採購單」這個動作打哪個系統。

| 方案 | PO / 供應商主檔在哪 | 優點 | 代價 |
|---|---|---|---|
| **A. PO 在 restaurant_api**（落地 docs/04 §3 的表） | 我們自己 | 進貨→扣庫存→推分錄一條龍，食安 `lot_no`/效期綁在 PO line；只推「金額」給 Odoo | 要實作 `suppliers`/`purchase_orders`/`purchase_order_lines` ORM（目前是 stop-gap） |
| **B. PO 在 Odoo**，收貨結果回灌 restaurant_api | Odoo | 用 Odoo 現成採購狀態機與供應商主檔，少寫程式 | 收貨要從 Odoo 同步回來才進庫存 ledger，**多一個分岔風險點**；食安欄位 Odoo 不原生支援 |

**我的推薦：方案 A。** 理由：

1. 進貨那一刻要同時寫 **庫存 ledger + `lot_no` 食安溯源 + 效期 FEFO**，這些**只有 restaurant_api 有**。讓 PO 在我們這邊，進貨是**一個 transaction 完成**，不跨系統。
2. Odoo 收「這筆進貨的**廠商發票分錄**」就好——它要的是應付帳，不是收貨作業。
3. `docs/04 §3` 的 `suppliers`/`purchase_orders`/`purchase_order_lines` **DDL 早就設計好**，只是還沒做成 ORM。落地它正好把 `stock` router 現在那個「synthetic `purchase_invoice_id` + JSON note」的 stop-gap（見 `specs/stock_intake_router.md` 註）升級成正規表——這件事**遲早要做**，跟 Odoo 整合剛好一起做掉。

→ 供應商主檔：**restaurant_api 為主**（因為 PO 在這），推一份到 Odoo `res.partner` 當記帳對象，靠 `odoo_partner_id` 對照。

（若日後有大量 B2B 外燴/報價/銷貨單需求，再考慮把「報價→銷貨」那條放 Odoo Sales，與內用 POS 分流。目前餐飲內用不需要。）

---

## 6. 三方勾稽：進貨 ↔ 廠商發票 ↔ 統一發票

這是整合最有價值的自動化，也是老闆最想要的「對帳」：

```
restaurant_api 進貨紀錄        Odoo 廠商發票            進項統一發票
(purchase_orders)      ─┐                      ┌─  (uniform_invoice_validator)
  supplier_id           ├─ invoice_number ─────┤   已在 restaurant_api 驗字軌/格式
  invoice_number ───────┘   統編 tax_id        └─  金額/稅額
  total                     金額對得起來嗎？        對得起來嗎？
```

- 三邊靠 `(supplier_tax_id, invoice_number)` 勾稽——這正是 `stock` spec 的 idempotency key。
- `uniform_invoice_validator`（已實作）先驗發票格式與字軌合法；驗過才推 Odoo 記應付。
- 老闆用嘴問：「這個月有哪張進貨對不到廠商發票？」→ Claude 跨兩系統 diff，抓出漏開/金額不符的單。

---

## 7. 分階段執行計畫（每階段可獨立驗收、獨立喊停）

| Phase | 目標 | 產出 | 驗收 = 綠燈 |
|---|---|---|---|
| **A. 站起 Odoo + 唯讀對話**（1 週） | Odoo Community 上線、Claude 能唯讀查 | Zeabur/自 docker-compose 部署 Odoo；接社群 Odoo MCP；`docs/11` 加 Odoo 服務 | 老闆能用嘴問「供應商清單」「應付總額」並拿到 Odoo 真資料 |
| **B. 整合骨架 + Stub**（1 週） | `integrations/odoo/` 四件套 + 分錄轉譯純函式 | `client.py`（ABC+Stub+Http+DI）、`postings.py`、單元測試全走 Stub | `make full-check` 綠；Stub 測試證明「進貨→正確借貸分錄」轉譯無誤，**零真實 Odoo** |
| **C. PO 表落地 + 進貨→應付自動推**（1–2 週） | docs/04 §3 三表落地；進貨即產生 Odoo 廠商發票 | `models/`+Alembic 遷移（供應商/PO/PO line）；`stock` router 升級去 stop-gap；`odoo_sync` job | 記一筆進貨 → Odoo 出現對應廠商發票分錄，金額/統編/發票號一致；重跑冪等不重複 |
| **D. 每日銷售 + 損失分錄彙總**（1 週） | POS 收班、報廢彙總自動入帳 | `odoo_sync_service` 擴充銷售/損失分錄 | 跑一天 `demo-flow` → Odoo 出現當日銷售日記帳 + 報廢損失，損益表對得起 `mv_daily_pnl` |
| **E. 三方對帳 + Dashboard**（1 週） | 勾稽自動化 + 總覽頁 | 對帳查詢；`restaurant_api` 拉 Odoo AP 做總覽 HTML（呼應貼文第六步） | 老闆用嘴問「對不到的單」得到正確清單；dashboard 顯示營運（我方）+ 應付（Odoo）合一視圖 |

> **建議節奏**：**先做 A + B**（低風險、可逆、不動生產資料），驗證「Claude+Odoo 對話體驗真的有用」再往下。C 之後才真正動到帳，要慎。

---

## 8. 部署（呼應貼文，接你的 docs/11）

- **Odoo**：Community 版。可用貼文的 **Zeabur**（AI 友善、一鍵模板），或用你既有的 `docker-compose.production.yml` 加一個 `odoo` + 獨立 `odoo_postgres` service（**Odoo 用自己的 PG，不共用 restaurant_api 的 PG**——資料庫層就隔離，避免誤 join）。
- **網址 / TLS**：Cloudflare（你 `docs/11` / `deploy-sid` 已在用 Cloudflare Pages/DNS），一個 `.com` 年約 NT$300，串 Odoo 子網域如 `erp.yourdomain.com`。
- **憑證**：`ODOO_URL / ODOO_DB / ODOO_API_KEY` 進 `.env`（gitignore 已擋）。**Odoo API key 用最小權限帳號**（只給會計/採購模組），不給 admin。

---

## 9. 風險紅線 / 反模式（違反任一條，整合就會變災難）

1. 🚫 **兩本庫存帳**：Odoo **絕不**存庫存數量。庫存 SSOT 永遠是 `stock_movements`。違反 → 盤點對不起來、成本錯亂。
2. 🚫 **把 ledger 搬去 Odoo**：append-only + `lot_no` 食安溯源是法遵資產，不可外移。
3. 🚫 **Claude 直連 Odoo 的 PostgreSQL 寫入**：一律走 Odoo external API（它有業務邏輯/驗證/分錄平衡），**不繞過**直接寫它的表。
4. 🚫 **同步推逐筆營運明細進 Odoo**：只推**金額彙總分錄**。逐筆留我方。
5. 🚫 **Claude 未經確認就記帳/開單**：動錢的寫入預設 dry-run + 人工點頭。
6. 🚫 **prod Odoo 憑證進 dev/DevSwarm 環境**：呼應 CLAUDE.md「DevSwarm 不指向 production credentials」。
7. 🚫 **共用一個 PostgreSQL**：Odoo 與 restaurant_api 各自的 DB，物理隔離。
8. 🚫 **非冪等同步**：每筆分錄帶 `external_id`，Odoo upsert，重跑安全。夜間 job 一定要能重放。

---

## 10. 給指揮官的一句話結論

**可以整合，而且方式很乾淨：Odoo 只當「財務後台」——會計總帳 + 廠商應付帳 + 供應商主檔 + 正規採購文件——這一塊正好是我們系統刻意沒做深、Odoo 現成最強的地方。**

**restaurant_api 繼續當營運真相（POS/庫存 ledger/食安/工時/顧客），把「進貨→應付、每日銷售、報廢損失、薪資」四種金額分錄自動推進 Odoo；Claude 掛兩個 MCP 坐在上面，老闆用嘴查兩邊、用嘴對帳。**

**鐵律只有一條要記：數量與作業歸我們，金額與帳務歸 Odoo，庫存 ledger 永遠不進 Odoo。**

**你只要拍一個板：採購單 header 放哪邊（§5，我推薦放 restaurant_api 並順手把 docs/04 §3 那三張早就設計好的表落地）。拍完，Phase A+B 兩週內可先跑起「站 Odoo + 用嘴唯讀查 + Stub 整合骨架全綠」，零風險驗證體驗，再決定要不要往「真的動帳」推。**

---

_相關文件：`docs/04_data_schema.md §3`（ERP 表 DDL）、`docs/06_execution_plan.md`（T1/T2 排程）、`docs/11_production_deployment.md`（部署）、`specs/stock_intake_router.md`（現行進貨 stop-gap）、`restaurant_api/integrations/line/messenger.py`（整合層範本）。_
