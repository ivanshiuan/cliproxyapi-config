# 21 — POS 工程案：專案管理章程（PM 制度）

> 本檔是「iCHEF 級 POS」工程案的**專案憲法**。角色、節奏、品質防線、WBS、復盤制度全在這。
> 拆解與架構見 `docs/20_ichef_teardown.md`。本檔只管「怎麼把它做出來、做到能上線自用、未來能賣」。
> 建立：2026-07-09。負責 PM：Claude（常駐角色 `.claude/agents/pos-pm.md`）。

---

## 1. 工作模式（Ivan 拍板的 PM 模型）

```
Ivan（指揮官/產品負責人）
  │  用人話下需求、審 PR、拍板決策 — 不需要看程式碼
  ▼
PM 工程師（Claude，本 session 常駐角色）
  │  需求 → 規格 → 拆工 → 派工 → 驗收 → 復盤，全流程負責
  ▼
執行層（subagents / DevSwarm / 直接實作）
     router-implementer、spec-writer、code-review、verify …
```

**溝通契約**：
- Ivan 說「做 X」→ PM 先回：**這一步的範圍、驗收標準、預估工作量**，超過一個 session 的工作先拆 milestone 再動工。
- 每個 milestone 完成 → 開 PR（人話標題 + 改了什麼/為什麼/怎麼驗證）→ Ivan 手機審核 Merge。
- 卡決策 → AskUserQuestion 給 2-4 個具體選項，不丟開放式問題。
- 每次收工 → `/handoff` 更新 `COMMANDER_HANDOFF.md`；每次開工 → `/morning`。

## 2. 品質防線（「不會有 bug」的工程化翻譯）

沒有「零 bug」，只有**每一層都攔一次**。上線自用的標準如下，任何一關不過就不進 main：

| 防線 | 工具 | 時機 |
|---|---|---|
| L1 靜態 | `make full-check`（ruff + pyright + pytest + alembic + smoke） | 每次 commit 前，無例外 |
| L2 審查 | `/code-review`（找正確性 bug）＋高風險改動加 `/security-review` | 每個 PR |
| L3 實跑 | `verify` skill：起真 server、打真 API、走完整流程（不是只跑測試） | 每個 milestone |
| L4 情境 | `make demo-flow` 擴充：POS 開桌→點餐→出餐→結帳→報表 一日全流程腳本 | 每個 Phase 收尾 |
| L5 復盤 | 見 §4 bug 復盤 SOP | 每個 bug、每個 Phase |

**Definition of Done（每個任務）**：功能可用 + 測試綠 + 家規遵守（Decimal/tenant_id/audit/軟刪）+ 文件更新 + demo 腳本能跑過。

## 3. WBS（工作分解，對照 docs/20 §6.4）

### P1 店員 POS（先做 — Ivan 拍板）
| # | 任務 | 驗收標準（摘要） | 狀態 |
|---|---|---|---|
| 1.1 | 桌位資料層：`dining_tables` + `table_sessions` + orders 加欄（order_type/table_session_id/channel）+ migration | alembic 升降級乾淨；模型過 pyright | ✅ 2026-07-09 |
| 1.2 | 菜單 CRUD API（categories/items，含軟刪與排序） | 整合測試綠；seed 可跑 | ✅ 2026-07-09 |
| 1.3a | 樓面/桌位層：桌位 CRUD + 開桌/結桌/取消/轉桌 + 桌況板（/tables） | 9 測全綠；既有訂單流程不動 | ✅ 2026-07-09 |
| 1.3b | 訂單升級：綁桌開單、逐項加點/改量/退菜（audit）— 建在 /tables 之上 | 既有進單流程不破壞；7 測 + 端到端 | ✅ 2026-07-09 |
| 1.4 | 現金結帳 + 找零 + 結桌（走 order_payments） | 端到端跑過；金額全 Decimal | ✅ 2026-07-09 |
| 1.5 | WebSocket hub + `order_events` append-only 事件表 | 斷線重連用 last_event_id 補拉 | ✅ 2026-07-10 |
| 1.6a | POS 前台 shell（輕量 Web：桌況圖/開桌/結桌/轉桌/菜單瀏覽，/pos） | 真 HTTP 端到端跑過一輪 | ✅ 2026-07-09 |
| 1.6b | POS 前台點餐/結帳（綁 P1.3b 訂單操作 + P1.4 結帳） | 前台帳單/加點/退菜/結帳找零 UI；API 全端到端驗過 | ✅ 2026-07-09 |
| 1.7 | P1 驗收：demo-flow 全流程 + 復盤 | L1–L5 全過 | ⬜ |

### P2 桌邊平板點餐（第二優先 — Ivan 拍板）
2.1 devices 裝置註冊（角色/綁桌）→ 2.2 顧客模式前端（綁桌 kiosk + 限定菜單）→ 2.3 桌位 QR 手機版 → 2.4 新單推播到 POS → 2.5 驗收復盤。
（P2 前拍板前端框架是否升級 React PWA。）

### P3–P6（順序照 docs/20 §6.4）
P3 KDS 螢幕 → P4 會員+折扣+**報表與後台匯出（CSV/Excel，Ivan 圈定）** → P5 金流+電子發票 → P6 訂位（訂金）+線上點餐。
人資（打卡/工時）後端已有，需求出現再排前端。

**已提前實作（Ivan 圈定，跳出 P4 順序先做）**
| 任務 | 驗收標準 | 狀態 |
|---|---|---|
| 營運報表 + 後台匯出：`/reports/sales`（日結/區間營收、付款方式明細、客單價）、`/reports/top-items`（熱銷排行）、`/reports/export/orders.csv`（BOM CSV 對帳匯出） | net_sales 與收銀台 `_compute_net_revenue` 一致（零分歧）；6 整合測 + L3 真伺服器驗過 | ✅ 2026-07-10 |

## 4. Bug 復盤 SOP（每個 bug 都要留下防再發機制）

1. **記錄**：bug 現象、root cause、影響範圍，一律寫進當次 PR 描述或 `COMMANDER_HANDOFF.md`。
2. **防再發**（至少一項）：加一個會抓到它的測試 / 加 lint 或 pyright 規則 / 把坑寫進 `CLAUDE.md`「經常踩到的坑」。
3. **Phase 復盤**：每個 Phase 收尾做一次 — 哪些估錯、哪些返工、哪條家規要新增。結論寫進本檔 §6。

## 5. 上線 gate（自用上線前的最後一關）

- 走 `docs/11_production_deployment.md` SOP（Docker + Cloudflare）。
- 真實資料演練：一家店、真菜單、營業一整天的平行試跑（POS 記一份、舊方式記一份，日結對帳一致才切換）。
- 備份與回復演練一次（DB dump → restore 成功）。
- 商用化（賣給其他店）前另立資安/個資審查，不在本檔範圍。

## 6. 復盤記錄（隨 Phase 累積）

- （空 — P1 收尾時寫第一筆）
