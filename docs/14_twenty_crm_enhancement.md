# 14 — 對標 Twenty CRM：CRM 關係經營層強化

> 決策（2026-11-25，Ivan）：把 CRM 強化**做進 repo**；時間軸 + 備註/任務 + 標籤**全部做**。
> 姊妹篇：`docs/12_hr_surpass_plan.md`（Aibou Crew）、`docs/13_haodian_pos_comparison.md`（好點 POS）。

---

## A. Twenty CRM 是什麼、強在哪

**Twenty**（twentyhq/twenty）是開源的 Salesforce 替代品，AGPL v3，提供 GraphQL + REST API。

| Twenty 能力 | 說明 |
|---|---|
| 標準物件 | People、Companies、Opportunities（pipeline）＋ Tasks、Notes |
| 關聯 | 一對多 / 多對多（一個 Company 多個 People） |
| **時間軸 / Activity** | 每個 record 一條把 email、會議、備註、任務串起來的時間軸 —— 核心體感 |
| Notes / Tasks | 具名、具時間的備註；指派、到期日、狀態的任務 |
| 自訂物件/欄位 | 自訂物件享有同等 API、視圖、權限、工作流觸發 |
| 視圖 | 表格 / 看板（kanban）、篩選、排序 |
| 工作流 | 觸發器 + 動作的自動化 |
| Email / 日曆同步 | 僅 People / Companies / Opportunities |

**本質**：Twenty 是 B2B 銷售 CRM —— 它的價值在「**關係經營**」：一打開客戶，就看到全部來往、待辦、備註。

---

## B. 我們現況 vs Twenty（強化前）

| 面向 | 我們強化前 | Twenty | 判斷 |
|---|---|---|---|
| 會員主檔 | ✅ Customer（LINE/電話/email/生日/tier） | People | 對齊 |
| **忠誠度** | ✅ 集點 ledger、儲值錢包、裂變推薦 | ❌ 沒有 | **我們領先**（餐飲專屬） |
| **分眾** | ✅ RFM 分眾 + 分眾廣播 | 篩選視圖 | **我們領先**（行為分眾） |
| 時間軸 | ❌ ledger 散落各表、無統一視圖 | ✅ 核心功能 | **我們缺 → 本次補** |
| 備註 | ❌ 只有單一 `notes` 欄位（會被覆蓋） | ✅ 多則具名 | **我們缺 → 本次補** |
| 任務/跟進 | ❌ 沒有 | ✅ Tasks | **我們缺 → 本次補** |
| 標籤 | ❌ 只有固定 tier enum | ✅ | **我們缺 → 本次補** |
| 接觸紀錄 | ❌ 沒有 | ✅ Activities | **我們缺 → 本次補** |
| 自訂欄位/看板/工作流 | ❌ | ✅ | Phase 2 |

---

## C. 本次補足（CRM 關係層）

### 資料層（migration `c4e9b2d71a05`，+5 表 → 共 42 表）
| 表 | 對標 Twenty | 用途 |
|---|---|---|
| `customer_notes` | Notes | 多則具名備註（作者=員工），軟刪 |
| `customer_tasks` | Tasks | 跟進任務：指派員工、到期日、open/done/cancelled、completed_at |
| `customer_tags` + `customer_tag_links` | Tags | 租戶自訂標籤（VIP/素食/過敏:花生/生日月），同名唯一 |
| `customer_interactions` | Activities | 手動接觸點（LINE/電話/臨櫃/email，進/出向） |

### API（`/crm/*`）
- **Notes**：建立 / 列表 / 修改 / 軟刪
- **Tasks**：客戶任務 CRUD + **跨客戶待辦清單**（店長看「我今天要跟進誰」）
- **Tags**：標籤定義 CRUD + 貼標 / 撕標 / 列客戶標籤
- **Interactions**：記錄 / 列表
- **Timeline** `GET /crm/customers/{id}/timeline` ← **核心**：把 **訂單、集點、儲值、推薦、備註、任務、接觸** 七種來源合併成一條時間軸，cursor 分頁

### 比 Twenty 更強的地方（餐飲專屬）
時間軸裡有 Twenty **沒有**的事件：**訂單金額、集點增減、儲值進出、推薦達成**。Twenty 的時間軸是「溝通紀錄」；我們的是「**溝通 + 消費 + 忠誠度**」—— 店長一眼看到「這位客人上週打來客訴、之後兩週沒來、儲值還剩 $800」。

---

## D. 一句話定位

> 好點 POS 的會員是一張名單；Aibou 沒有會員；Twenty 有關係經營但不懂餐飲。
> **我們是唯一把「消費 + 忠誠度 + 關係經營」放在同一條客戶時間軸上的餐飲系統。**

---

## E. Phase 2（本次刻意不做）
- 自訂欄位引擎（custom objects / fields）
- 看板 / 表格視圖 UI（本次純後端 API）
- 工作流自動化（例：「標籤=VIP 且 30 天未到店 → 自動建跟進任務」—— 可接既有 `jobs/` APScheduler）
- LINE 訊息自動寫入 interactions（接 `integrations/line/` webhook）
- 用標籤做分眾廣播（接既有 `/membership/segments/{segment}/broadcast`）

---

## 來源
- [Twenty — data model docs](https://docs.twenty.com/user-guide/data-model/overview)
- [Twenty — standard & custom objects](https://twenty.com/user-guide/section/objects/standard-objects)
- [twentyhq/twenty on GitHub](https://github.com/twentyhq/twenty)
- [TechCrunch：Twenty is building an open source alternative to Salesforce](https://www.techcrunch.com/2024/11/18/twenty-is-building-an-open-source-alternative-to-salesforce/)
