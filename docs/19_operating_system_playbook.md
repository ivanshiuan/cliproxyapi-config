# 19 — 營運作業系統 Playbook

> **這份文件是給 Ivan 跟未來團隊看的**，不是給工程師看的。
> 不談 code、不談 SQL、談的是：**這套系統怎麼變成我們每天用的紀律，
> 對業務帶來什麼好處，對外怎麼講。**
>
> 生效日：merge PR #11 / #12 / #14 之後。這份文件是「戰略指揮官手冊」，
> 6 個月更新一次。

---

## 一句話總結

> 我們把 F&B 後端從「一個 API」升級成 **「多角色 + 可稽核 + 可規模化的營運作業系統」**。
> 過去別家餐廳有 POS，我們有 POS + 身分認證 + 權責分工 + 素材大腦 + 全流程稽核。

---

## 0. 為什麼現在必須做這個（不是選項題）

| 情境 | 沒有這套會發生 | 有這套會發生 |
|---|---|---|
| 分店店長離職，把系統密碼帶走 | 全公司改所有密碼、爛帳 | 撤 role、繼任者直接接手、audit_log 追得到操作 |
| 加盟主想開第二家 | 每家系統獨立、資料不打通、規模不了 | 新 tenant、新 role assignments、4 小時 onboarding |
| 收到食品藥物署稽查食安問題 | 翻紙本 + 罵員工 | 60 秒調出 audit_log：誰在什麼時候動了什麼 |
| 想跟投資人 pitch | 「一家還可以的餐廳 SaaS」 | 「多店可規模化、每筆操作可稽核的餐飲營運 OS」 |
| 行銷團隊要找一張菜色圖 | Google Drive 翻一小時 | 打「暖色調湯品 hero」，30 秒 |
| 供應商想看訂貨量 | Excel 寄來寄去 | 給 supplier role，只看得到自己供貨、跨店可比 |

**這不是「有做很酷」而已，是「沒做就會撞牆」。**

---

## 1. 這一套是什麼（給人講的白話版）

我們有三個核心零件，合起來變成一個系統：

### 1.1 Auth（身分認證）
- **是什麼：** 每個員工有自己的 email + 密碼，登入拿到「身分證」（JWT token）
- **為什麼重要：** 每個操作都有名字。不是「有人改了菜單」，是「A 店的 Manager 王小姐今天 15:22 改的菜單」
- **對業務意義：** 沒有這個，一切責任稀釋、一切追溯無門

### 1.2 RBAC（角色 + 權限）
- **是什麼：** 5 個系統角色（owner / store_manager / marketing / supplier / staff）+ 22 個權限
- **為什麼重要：** 不再是「登入者能做所有事」。行銷只能看行銷該看的、供應商只看自己的、店長只操作自己店的
- **對業務意義：** 責任分離；不是靠信任，是靠系統擋

### 1.3 Visual DAM（素材大腦）
- **是什麼：** 上傳圖 → 自動理解內容 → 用文字或圖搜圖（中文可用）
- **為什麼重要：** 行銷素材從「翻硬碟」變「下 query」
- **對業務意義：** 每個素材的 ROI 可量化（用了幾次、找了幾次）；素材庫從死資產變活資產

### 1.4 為什麼三個要一起做（單獨做都不對）
- 只有 Auth 沒 RBAC = 每個員工都是超管，等於沒鎖
- 有 Auth+RBAC 沒 Visual = 系統保護嚴密的空盒子
- 有 Visual 沒 Auth+RBAC = 供應商能翻到我們品牌素材庫

**三個綁在一起 → 完整營運系統。** 缺一角就沒有紀律。

---

## 2. 對每個角色實際帶來什麼好處

### 2.1 給 Owner（Ivan 自己）

| 過去（沒這套） | 現在（有這套） |
|---|---|
| 睡不安穩，怕員工亂動 | 高風險操作全部要 owner permission，睡得安穩 |
| 加盟複製難，每家從零 | 建新 tenant → seed role → 4 小時 onboarding |
| 對投資人講「有一家店」 | 對投資人講「一套可規模化營運 OS 上跑一家店」— 差 10× 估值 |
| 稽查找不到憑證 | audit_log 30 天查到底、每筆操作能舉證 |
| 每次要調權限都要問工程師 | 用 admin UI（Phase 3）自己 grant / revoke |

### 2.2 給 Store Manager（店長）

| 過去 | 現在 |
|---|---|
| 什麼都能做但不知道邊界 | 權限表 give me clarity：這 15 個 permissions 是你的 |
| 出錯背黑鍋 | 有 audit_log，證明操作正確或不正確 |
| 跨店支援時混亂 | 支援期間短暫 grant，離開自動 revoke |

### 2.3 給 Marketing（行銷）

| 過去 | 現在 |
|---|---|
| 素材翻 Google Drive 一小時 | 中文 query「熱湯」30 秒 |
| 品牌素材外流風險 | brand_ref 素材只有 owner + marketing 看得到 |
| 素材上傳完就消失 | 用了幾次、被搜幾次都能量化 |
| 品牌一致性靠人眼盯 | 未來 Phase 3 自動分數化 |

### 2.4 給 Supplier（供應商 / 供應鐘）

| 過去 | 現在 |
|---|---|
| 我們 Excel 給你、你 Excel 給我 | 你有 login，只看得到你供貨的 stock 資料 |
| 對帳靠信任 | 一切在同一個系統，query 即算 |
| 不知道自家品項在多少家店賣 | 跨店 dashboard（未來） |

### 2.5 給 Staff（外場 / 工讀）

| 過去 | 現在 |
|---|---|
| 有可能誤操作到別人的區塊 | 系統擋，「你不能做這個」明確有理由 |
| 忘密碼要找店長重設 | 未來 SMS 重設（Phase 3） |
| 打錯卡沒憑證 | audit_log 記得清清楚楚 |

---

## 3. 對外怎麼講（Ivan 的 pitch 話術庫）

### 3.1 對投資人

> 「我們做的不是餐廳，是**多店可規模化的餐飲營運作業系統**。
>
> 一般 F&B SaaS 給你一支 POS API。我們給你：POS API + 每個員工的身分認證 +
> 5 種角色的權責分工 + 全流程稽核 log + AI 素材大腦。
>
> 相同的 code base，可以支撐 1 家店，也可以支撐 100 家加盟。
> 加盟展店 unit economics 因此可以計算。」

### 3.2 對加盟主

> 「你不用自己蓋數位化系統。總部給你：
> - 一套已上線的營運 OS
> - 你的店開通後，你是這家店的 owner role
> - 你的員工 email 一寄，直接登入就用
> - 你不用擔心員工亂動，系統會擋
> - 對食藥署稽查、勞檢，我們把 audit 準備好」

### 3.3 對記者 / 媒體

> 「台灣 F&B 業第一套內建 RBAC + audit_log + 中文 AI 素材檢索的營運系統。
> 每個操作可追溯、每個角色權責清楚、每張素材可量化 ROI。
> 不是喊 AI，是 AI 增強的營運紀律。」

### 3.4 對員工（內部宣導）

> 「我們上了新系統。三件事影響你：
> 1. 你有 email 帳號了，每次操作系統都要登入
> 2. 你只能做你角色該做的事，系統會擋不該做的
> 3. 每個操作系統會記錄，你做對了有憑證、做錯了能重新學
> 這是保護你，也是保護店。」

---

## 4. 有紀律的日常操作 SOP（最關鍵一節）

> **這套系統會不會爛掉，看 Ivan 跟未來營運長有沒有紀律。**
> 沒有這節，前面全部白做。

### 4.1 每日（5 分鐘）

| 時段 | 誰 | 動作 |
|---|---|---|
| 早上開店 | 店長 | login 系統、看昨日 audit_log 有沒有紅字 |
| 每次上傳素材 | 行銷 | 一定要標 `kind` + `tags`，違反回撤 |
| 每次 grant role | Owner | 給前先問「這人真的需要嗎？離職前忘記撤怎麼辦？」 |

### 4.2 每週（30 分鐘，星期一晨會）

- **Role 變動 review**：這週誰被加了角色、為什麼、離職名單裡是不是還有活躍 role
- **失敗登入 top 10**：看有沒有暴力破解跡象、有沒有離職員工還在試登入
- **新素材上傳量**：有沒有暴增暴跌、標籤是否合理

### 4.3 每月（1 小時，月底）

- **Permission catalog 檢視**：這個月有沒有出現「這件事我想擋但擋不了」→ 新增 permission
- **Role 使用率報表**：有沒有 role 沒人用（可以退休）、有沒有 role 大家都想要（可能分太細）
- **素材使用率 top / bottom 10**：垃圾素材清掉、熱門素材保護好
- **Audit_log 隨機抽 20 筆**：讀懂、能還原、記錄異常
- **JWT secret 檢查**：production 用的 secret 有沒有洩漏跡象

### 4.4 每季（半天）

- **角色設計整體 review**：有沒有新職能要新 role（例：加了外送業務，可能要 delivery_manager）
- **Audit_log 完整回顧**：抽 100 筆看，是不是所有敏感操作都有紀錄
- **JWT secret rotation**：轉一次 secret，強制所有裝置重新登入
- **災難演練**：DB 撈掉能不能還原？restore 出來 audit_log 完整嗎？

### 4.5 每年（1-2 天）

- **完整合規 audit**：食安溯源 + 勞基工時 + 個資 + 這套 auth 一起看
- **紅隊演練**：找人扮演惡意內部 / 外部攻擊者，測試防禦
- **戰略決策 review**：docs/03_roadmap.md 有沒有偏航

### 4.6 紀律工具箱（自動化，減少人為遺忘）

| 工具 | 做什麼 | 何時上 |
|---|---|---|
| **權限變更提醒**（LINE 推播 owner）| 任何 grant/revoke 立即通知 | Phase 3 加 |
| **每週 audit 摘要 email** | 週日晚上自動寄異常 | Phase 3 加 |
| **離職清理 checklist** | HR 標離職 → 系統自動生成撤 role 清單 | Phase 3 加 |
| **每月 permission 使用熱圖** | 哪個 permission 被用了幾次 | Phase 4 加 |
| **JWT secret rotation 提醒** | 90 天提醒一次 | 建 CronJob 即可，Phase 2 就上 |

---

## 5. 從單一功能整合成「營運系統」的策略

### 5.1 資料流（怎麼串起來）

```
   員工登入
     │
     ▼
   [ Auth ] 產生 JWT token（帶 employee_id + tenant_id + roles + permissions）
     │
     ▼
   任何 API 呼叫（POST /orders、POST /visual/assets、GET /stock...）
     │
     ▼
   [ RBAC ] 檢查 token 的 permission 是否包含此操作
     │
     ├─── 沒權限 → 403，audit_log 記「拒絕」
     │
     └─── 有權限 → 執行
              │
              ▼
   [ Service 層 ] 執行業務邏輯
              │
              ▼
   [ audit_service ] 敏感操作寫 audit_log（誰、什麼時候、對哪個 tenant、做了什麼）
              │
              ▼
   [ PG 儲存 ]  數據落地
              │
              ▼
   [ Visual DAM ] 如果是素材相關，額外計算 embedding、存 pgvector
```

**這個流每一步都是紀律。** 沒 Auth，第 2 步做不到；沒 RBAC，第 3 步無意義；沒 audit，第 5 步事後追不到。

### 5.2 未來擴充路徑（Phase 3 之後）

當 Auth + RBAC + Visual 三大基礎建好，後面的東西就是「掛」上去，不是「重建」：

| Phase | 新功能 | 依賴 | 難度 |
|---|---|---|---|
| **Phase 3** | OCR（收據/手寫單/中文菜單）| Auth（誰上傳）+ audit（金額入帳追溯） | 中 |
| Phase 3 | 智能裁切（響應式 hero）| Visual DAM 已在 | 低 |
| Phase 3 | 品牌一致性審核 | Visual DAM + RBAC brand_ref | 低 |
| **Phase 4** | 加盟展店（多 tenant）| Auth + RBAC 已 tenant-scoped | 中 |
| Phase 4 | 跨店營運 dashboard | audit_log + Visual | 中 |
| Phase 4 | Supplier portal（供應商入口） | RBAC supplier role 已在 | 低 |
| **Phase 5** | 開放 API 給第三方 | Auth JWT 已在 | 中 |
| Phase 5 | 對外 SaaS（別家餐廳來訂閱） | multi-tenant + billing | 高 |
| Phase 5 | AI 智慧助理（自然語言操作系統） | 全 stack | 高 |

### 5.3 為什麼一定是這個順序（策略邏輯）

1. **Auth 必須第一**：其他一切都需要「你是誰」。沒它，後面全空
2. **Visual 早點做**：不是因為技術要，是因為**行銷團隊需要立刻看到回饋**。工程師看得到 auth，行銷團隊看不到 auth；不能只做「工程師滿意」的東西
3. **Phase 3 才能規劃**：手上有 Auth + Visual 的實戰數據後，才知道哪個情境痛
4. **Phase 4 加盟複製**：Auth + RBAC 已 tenant-scoped，加盟只是「開新 tenant」而不是「重新蓋系統」
5. **Phase 5 對外**：等自家跑順，對別家餐廳收費才有底氣

---

## 6. 完整落地時間表

### 6.1 Auth + Visual 上線期（Week 1-8）

| 週次 | 目標 | 完成標誌 |
|---|---|---|
| Week 0（現在） | 3 個 PR open：#11（spec）、#12（PR-A schema）、#14（PR-B JWT）| Ivan review + merge |
| Week 1 | PR-A + PR-B merge、跑通登入 | 自己能 login 拿 JWT |
| Week 2 | PR-C：既有 router 接認證 | orders/stock/clock 全掛 auth |
| Week 3 | PR-D：test fixture 改造完成 | CI 全綠 |
| Week 4 | PR-E：bootstrap CLI + docs 完成 | 第一個 owner 帳號建成 |
| Week 4 | 視覺 A1-A4：DAM backend 實作 | 25 條 AC 全過 |
| Week 5 | 視覺 B：批次匯入 script + 灌 100-1000 張素材 | pgvector 有資料可搜 |
| Week 6 | 視覺 C1：搜尋介面 UI（Streamlit / Next.js）| 行銷團隊點進去能用 |
| Week 6 | 視覺 C2：物件儲存（R2 或 MinIO） | 搜尋結果有 thumbnail |
| Week 7 | 視覺 D：雲端 L4 部署 + 上線 | 行銷團隊真的用 |
| Week 8 | UAT + 收回饋 | 決定 Phase 3 打哪個情境 |

### 6.2 Phase 3（Week 9-16）

由 Week 8 的行銷團隊回饋決定順序。候選：
- OCR 收據 / 手寫單
- 智能裁切
- 品牌一致性
- 競品 landing page 監控

### 6.3 Phase 4（Month 4-6）

- 加盟展店複製工具
- 跨店 dashboard
- Supplier portal

### 6.4 Phase 5（Month 7-12）

- 對外 API
- 對外 SaaS
- AI 助理

---

## 7. 每階段 KPI + 檢查點

### 7.1 Auth 上線後（Week 4）

| KPI | 綠 | 黃 | 紅 |
|---|---|---|---|
| 既有 router auth 覆蓋率 | 100% | 80-99% | < 80% |
| audit_log 記錄完整度（敏感操作）| ≥ 99% | 90-98% | < 90% |
| 失敗登入日均 | < 20 | 20-100 | > 100（有攻擊）|
| JWT 相關錯誤日均 | < 5 | 5-30 | > 30（設定有問題）|

### 7.2 Visual 上線後（Week 8）

| KPI | 綠 | 黃 | 紅 |
|---|---|---|---|
| 素材檢索 recall@10 | ≥ 0.85 | 0.7-0.84 | < 0.7 |
| 行銷團隊搜尋時間中位數 | < 30 sec | 30-60 sec | > 60 sec |
| 每週搜尋次數 | > 50 | 10-50 | < 10（沒人用）|
| 上傳素材率 | > 20 張/週 | 5-19 | < 5（庫沒長）|

### 7.3 一個月後（Week 12）

| KPI | 綠 | 黃 | 紅 |
|---|---|---|---|
| 系統掉線 | 0 分鐘 | < 30 分鐘/月 | > 30 分鐘 |
| audit_log 抽驗完整率 | 100% | 90-99% | < 90% |
| 團隊自訴問題數 | < 5 | 5-15 | > 15 |
| 行銷團隊「不想回舊系統」的自訴 | 有 | 沒 | 想回去（表示做的方向錯）|

### 7.4 半年後（Month 6）

- 有沒有第二家店（加盟或直營）成功接上
- 有沒有一次外部稽查用 audit_log 過關
- 對外 pitch 有沒有拿到過投資意向書

---

## 8. 紅線清單（絕對不能做）

| 紅線 | 為什麼 |
|---|---|
| **owner role 給超過 3 個人** | Owner 是最高權限，多一個人多一個攻擊面。技術上可，紀律上不可 |
| **JWT_SECRET commit 進 git** | 一次就完蛋。所有 token 都得作廢重發 |
| **audit_log 手動 DELETE** | 直接違反「不變法則」+ 稽核就爆 |
| **關掉 auth_enforcement 上 prod** | 等於整個 auth 系統作廢 |
| **給 supplier role 存取其他 supplier 資料** | 商業機密洩漏 |
| **關掉 tenant_id filter** | 多租戶隔離破功、加盟主看到彼此資料 |
| **skip audit_log 加速性能** | 事後追不到 = 不能舉證 = 稽查爆 |
| **員工離職不撤 role** | 累積下來變隱形超管 |
| **同個 email 綁到兩個 employee** | credential UNIQUE 已擋，但社交攻擊要小心 |
| **JWT TTL 拉超過 1 小時** | permission 變更等太久生效，緊急情況救不了 |

---

## 9. 綠燈清單（一定要做）

| 綠燈 | 何時 |
|---|---|
| 每次 grant role，log 到 LINE owner 群組 | 立即 |
| 每次高風險操作（void 訂單 / delete 素材 / grant owner）多加一層確認 | 立即 |
| audit_log 30 天內查得到、90 天內可 restore | Week 4 |
| 每月抽 audit_log 20 筆手動看 | 每月 |
| JWT secret 每 90 天 rotate 一次 | 每季 |
| 每個新員工上線前先建 employee row、然後 credential、然後 grant role（順序不能反） | 每次 |
| PR 描述必須引用對應 spec | 每個 PR |
| 週日晚上跑「離職員工 role check」cron | 每週 |

---

## 10. Cheat Sheet（Ivan 桌前貼一張）

### 10.1 5 個系統角色速查

| Role | 誰 | 能做什麼（一句話） |
|---|---|---|
| `owner` | Ivan、少數共同創辦人 | **全部 22 個 permission** |
| `store_manager` | 各店店長 | 店內營運全套（不含 auth 管理） |
| `marketing` | 行銷團隊 | 素材 + 訂單讀取 + 品牌素材 |
| `supplier` | 供應商 | 只讀自己相關的庫存 |
| `staff` | 一線員工 | 開單、結單、打卡 |

### 10.2 「這個能不能做」決策樹

```
有人問「我能不能做 X」
    │
    ▼
X 是不是敏感操作？（改金額 / 撤單 / 改權限 / 匯出資料）
    │
    ├─ 是 → 要 owner 或 store_manager，且要 audit_log
    │
    └─ 否 → 看對方 role
              │
              ├─ owner / store_manager → 通常可
              ├─ marketing → 只素材相關
              ├─ supplier → 只自家供貨
              └─ staff → 只前場點單
```

### 10.3 遇到問題查誰

| 問題 | 找誰 / 看哪 |
|---|---|
| 忘密碼 | 找 owner，未來 SMS 重設 |
| 我明明有權限卻被擋 | 看 `/auth/me` 回什麼 permissions，缺哪個 |
| 系統掛了 | `/health/ready` + Cloudflare status |
| 稽查來問 | `docs/08_safety_compliance.md` + audit_log query |
| 想加新 role | 走 spec 流程，不要繞過 |

### 10.4 Ivan 每天問自己一遍

- [ ] 昨天的 audit_log 有沒有看
- [ ] 這週有沒有新離職員工的 role 還沒撤
- [ ] 這月有沒有新增 permission、有沒有 spec
- [ ] 有沒有偷懶想「這次先 skip audit」

**這 4 個問題答錯任何一個 → 系統開始爛。**

---

## 11. 給團隊的一段話

> 我們不做「有 AI 的餐廳」，我們做「有紀律的營運系統，順便用 AI」。
>
> 紀律不是拘束，是自由。因為每個人知道自己該做什麼、不該做什麼，
> 而系統會幫你擋住不該做的事，你就能安心把該做的事做好。
>
> 如果我們紀律夠、系統夠穩、可稽核夠強，我們就能開第二家、第三家、
> 第五十家。如果我們紀律鬆、系統破洞、什麼都靠信任，開第二家就會爛。
>
> 這份 playbook 每 6 個月修訂一次。你有想法，寫進 PR。
> 沒有紀律的 SaaS，只是一組偶然還沒壞的程式碼。

---

## 附錄 A：相關文件索引

- `docs/00_vision.md` — 我們是誰
- `docs/03_roadmap.md` — Phase 0-5 路徑
- `docs/04_data_schema.md` — 25 表 DDL
- `docs/08_safety_compliance.md` — 合規 SOP
- `docs/11_production_deployment.md` — 部署 SOP
- `docs/18_vision_encoder_strategy.md` — 視覺模型選型（PR #11）
- `specs/auth_rbac_system.md` — Auth/RBAC 完整 spec（PR #11）
- `specs/visual_asset_embedding.md` — DAM spec（PR #11）

## 附錄 B：本文件更新記錄

| 版本 | 日期 | 修訂 |
|---|---|---|
| 1.0 | 2026-06-27 | 首版，涵蓋 Auth + RBAC + Visual DAM 三塊上線 playbook |

—— end of 19_operating_system_playbook.md ——
