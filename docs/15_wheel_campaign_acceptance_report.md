# 開幕引流輪盤抽獎 — 完整驗收報告

> 對象：指揮官（老闆）
> 日期：2026-06-21
> 分支：`claude/launch-wheel-game-campaign-t7octp` ｜ PR：[#1](https://github.com/ivanshiuan/cliproxyapi-config/pull/1)
> 狀態：**可上線**（上線前只需設 2 個密鑰，見 §8）

---

## 1. 一句話總結

門口擺一張 QR Code → 客人掃碼玩輪盤 → **抽獎當下自動變會員** → 抽中的獎進「券錢包」→ 來店向櫃台出示券碼核銷吃飯。
店長有一個**上鎖的後台**可以：建活動、排時間、調中獎率、看即時成效、匯出對帳 CSV、櫃台核銷。

整套是**真的後端**（PostgreSQL + FastAPI），不是假畫面：金額用 `Decimal`、時間用台北時區、抽獎用資料庫列鎖防作弊與超發。

---

## 2. 營業需求對照表

| 營業需求 | 是否達成 | 證據 / 說明 |
|---|:---:|---|
| **開幕引流**：低成本把路過客變顧客 | ✅ | 門口 QR → `/demo/?campaign=<id>`；可一鍵產生 QR 與 A4 海報 |
| **掃碼即加入會員**（不用填表） | ✅ | 首次抽獎自動建會員（用 LINE id／手機）；實測 8 人抽獎 = 8 位新會員 |
| **抽獎機制**：每日一抽、可控中獎率 | ✅ | `daily_spin_limit`（台北日界）＋加權隨機；實測中獎率 75% |
| **大獎要稀有、不能爆發** | ✅ | 權重 + 總量/每日配額。實測：免單(3200)、雙人套餐(1280) **0 中**、配額完整；便宜飲料中 4 次 |
| **券有時效、來店才能用** | ✅ | `valid_from = 抽中日 + offset`、`valid_until = +效期天數`；夜間 job 自動過期 |
| **每次來店只核銷一張**（挑最大獎） | ✅ | 同會員同活動同日限一張，DB 唯一索引兜底；錢包依價值高→低排序 |
| **櫃台核銷由店員操作**（非顧客自助） | ✅ | 核銷端點已上鎖（需登入）；顧客頁改成「至櫃台出示券碼」 |
| **店長能自己營運**（不用工程師） | ✅ | 後台：建活動／排程／調賠率／看成效／匯出 CSV／核銷，全圖形化 |
| **後台要有權限保護** | ✅ | 共享通行碼 → 簽章 session cookie；所有管理/櫃台端點上鎖 |
| **看得到成效**（投了多少、回收多少） | ✅ | 即時儀表板：抽獎數/中獎率/核銷率/已核銷價值 vs 未核銷負債 |
| **對帳**（會計要券清單） | ✅ | 一鍵匯出 CSV（UTF-8 BOM，Excel 直接開、中文不亂碼） |
| **台灣在地**（金額/時間/語言） | ✅ | 金額 `Decimal`、時間台北時區、介面繁體中文 |
| **LINE 推播**（抽中通知/每日召回） | 🟡 | 通道已接好，目前走 Stub；填 `LINE_CHANNEL_ACCESS_TOKEN` 即真實發送 |
| **多分店 / 多租戶隔離** | 🟡 | 資料表已預留 `tenant_id`/`store_id`；MVP 單租戶，RLS 待開（D2 決策） |
| **正式品牌前端 / 自家網域** | 🟡 | 目前是自包含 demo 頁；可直接沿用或換成品牌版前端 |

✅ = 已達成並實測　🟡 = 部分達成 / 需設定或後續決策

**結論**：**核心營業需求全部達成且可實機運作**。三個 🟡 都不是「沒做」，而是「等你拍板的設定/決策」（LINE token、多店 RLS、品牌前端）。

---

## 3. 兩條使用者旅程（逐畫面走查）

### 🧑 顧客端（手機，無需登入）— `/demo/?campaign=<id>`

1. **進場**：掃門口 QR → 開啟手機輪盤頁。標題、活動名稱、「每日一抽 · 抽中即加入會員 · 來店核銷」。
2. **輸入身分**：填 LINE id 或手機（首抽即入會）。
3. **轉輪盤**：按「抽獎一次」→ 輪盤旋轉動畫 → 停在中獎扇區 → 跳出結果。
   - 顧客**看不到中獎機率**（公開版面只給獎項名稱與排序，機率藏在後端）。
4. **我的獎品錢包**：列出抽中的券（獎名、券碼、效期、狀態）；可用的券顯示「**至櫃台出示**」徽章（不能自助核銷）。
5. **再抽**：同日再按會被擋（每日一抽）。

### 🧑‍💼 店長端（電腦/平板，需登入）— `/demo/admin.html`

1. **登入閘門**：開頁先要求輸入「店長通行碼」（錯誤會擋、正確才進）。登入狀態存在簽章 cookie，12 小時內免再登入；右上角有「登出」。
2. **活動列表**（左欄）：列出所有活動，可依狀態篩選，點選查看。
3. **建立新活動**（左欄）：名稱、slug、每日抽獎次數、券有效天數、**開始/結束時間（台北）**、每日推播訊息 → 建立（草稿）。
4. **活動詳情**（右欄，選定後）：
   - **標頭 + 生命週期**：名稱、狀態徽章、上線/結束/轉草稿按鈕。
   - **⏰ 活動排程**：顯示並可編輯開始/結束時間（台北）；超出區間抽獎會被擋。
   - **連結**：顧客抽獎頁 / 門口 QR / 列印海報。
   - **📊 活動成效**（即時）：六格數字 + 券漏斗長條 + 「⬇ 匯出券 CSV」「↻ 重新整理」。
   - **🎯 輪盤獎項**：表格可**直接改權重/價值/配額**後按 💾 存檔（活動進行中即時調賠率）；可停用/啟用單一獎項；下方可新增獎項。
   - **🧾 櫃台核銷**：輸入/掃描券碼 → 查詢 → 確認核銷（每位會員每次來店限一張）。

---

## 4. 本次新增的五大後台能力（這次 session 的成果）

| # | 能力 | 端點 / 位置 | 商業價值 |
|---|---|---|---|
| 1 | **後台登入閘門** | `POST /admin/login`、`/logout`、`/session`；`admin.html` 登入遮罩 | 沒上鎖的後台＝任何人都能改活動/核銷。共享通行碼最適合「店長共用一台機器」場景 |
| 2 | **即時成效儀表板** | `GET /campaigns/{id}/stats` + 成效面板 | 行銷活動沒數據＝瞎做。看得到抽獎/中獎/核銷/已花 vs 未回收 |
| 3 | **賠率即時微調** | `PATCH /campaigns/{id}/prizes/{pid}` + 可編輯表格 | 大獎中太兇可即時調低權重/縮配額，不用找工程師 |
| 4 | **券清單 CSV 匯出** | `GET /campaigns/{id}/vouchers.csv` + 匯出鈕 | 會計對帳、跟現場核銷紀錄勾稽 |
| 5 | **活動排程 UI** | 建立表單 + 詳情 `starts_at/ends_at` | 「開幕日才開跑、活動結束自動停」一鍵設定，超窗抽獎自動擋 |

---

## 5. 實機驗證證據（真實 API 回應）

以下是剛剛在真實伺服器上跑出來的結果（8 人抽獎、核銷 3 張）。

**公開輪盤版面**（顧客頁用，**不含機率** — 防作弊）：
```
免單四人套餐 / 雙人套餐 / 和牛一盤 / 招待飲料一杯 / 銘謝惠顧
```

**成效儀表板 `GET /stats`**：
```json
{
  "total_spins": 8, "unique_players": 8,
  "winning_spins": 6, "win_rate": 0.75,
  "vouchers_minted": 6, "vouchers_redeemed": 3, "vouchers_active": 3,
  "redemption_rate": 0.5,
  "value_redeemed": "1260.0000", "value_outstanding": "180.0000",
  "prizes": [
    {"name":"免單四人套餐","value":"3200","awarded_count":0,"total_quota":3,"remaining":3},
    {"name":"雙人套餐","value":"1280","awarded_count":0,"total_quota":10,"remaining":10},
    {"name":"和牛一盤","value":"600","awarded_count":2},
    {"name":"招待飲料一杯","value":"60","awarded_count":4},
    {"name":"銘謝惠顧","value":"0","awarded_count":0}
  ]
}
```
> 👉 **重點**：貴的大獎（3200 / 1280）**一次都沒中、配額完整**，便宜飲料中最多 → **稀有度控管確實生效**，行銷成本可控。

**券清單 CSV**（Excel 可直接開、中文正常）：
```
code,status,prize_name,value_estimate,customer_id,valid_from,valid_until,redeemed_on,created_at
9TC36U46,active,招待飲料一杯,60.0000,...,2026-06-21,2026-07-05,,2026-06-21T09:12:04Z
```

**其他已實測**（前面對話逐項驗過）：
- 登入閘門：未登入建活動→`401`；輸入正確通行碼→發 cookie→可操作。
- 時區：輸入 `10:00`（台北）→ DB 存 `02:00Z`（正確 -8h）。
- 活動排程：把開始時間設未來 → 抽獎被擋 `409 campaign has not started yet`。
- 賠率微調：權重 10→3、價值 300→500、配額 ∞→50，即時生效。

---

## 6. API 端點總表

| 端點 | 權限 | 用途 |
|---|:---:|---|
| `GET /campaigns/{id}/wheel` | 🌐 公開 | 顧客頁輪盤版面（不含機率） |
| `POST /campaigns/{id}/spin` | 🌐 公開 | 抽獎一次（首抽自動入會） |
| `GET /campaigns/{id}/vouchers?customer_id=` | 🌐 公開 | 會員看自己的錢包 |
| `GET /campaigns/{id}/qr.svg`、`/poster` | 🌐 公開 | 門口 QR / 列印海報 |
| `POST /admin/login`、`/logout`、`/session` | 🔑 通行碼 | 後台登入/登出/查狀態 |
| `POST/GET/PATCH /campaigns`、`/{id}` | 🔒 上鎖 | 建立/列出/查/改活動（含排程、狀態） |
| `POST/GET/PATCH /campaigns/{id}/prizes` | 🔒 上鎖 | 新增/列出/改獎項（賠率微調） |
| `GET /campaigns/{id}/stats` | 🔒 上鎖 | 成效儀表板 |
| `GET /campaigns/{id}/vouchers.csv` | 🔒 上鎖 | 匯出券清單對帳 |
| `GET /campaigns/{id}/vouchers/by-code/{code}` | 🔒 上鎖 | 櫃台掃碼查券 |
| `POST /campaigns/{id}/vouchers/{vid}/redeem` | 🔒 上鎖 | 櫃台核銷 |

---

## 7. 安全與資料正確性

- **權限**：後台/櫃台端點全部 `require_admin`（驗證 HMAC 簽章 + 過期）；顧客面維持公開。
- **金額**：一律 `Decimal`（`Numeric(14,4)`），永不用 float；輸入端拒絕 float。
- **時間**：DB 存 UTC、對外台北時區；台北固定 +08:00（無日光節約）。
- **防作弊/超發**：抽獎對活動列 `SELECT ... FOR UPDATE` 序列化 → 每日計數與配額扣減無競態；核銷對券列上鎖 + 唯一索引兜底。
- **稽核**：建活動/改獎項/核銷都寫 append-only 稽核紀錄。
- **品質關卡**：400 個 pytest 全綠、ruff + pyright 乾淨。

---

## 8. 上線清單（部署前）

1. 🔴 **設 2 個密鑰**（最重要，沒設＝任何人用預設密碼進後台）：
   ```bash
   RESTO_ADMIN_PASSCODE=<發給店長的通行碼>
   RESTO_SESSION_SECRET=$(openssl rand -hex 32)
   ```
2. 跑遷移：`make db-migrate`
3. 建活動：`make wheel-demo`（或從後台手動建）
4. 啟動：`make api`
5. （選配）填 `LINE_CHANNEL_ACCESS_TOKEN` 開啟真實 LINE 推播
6. 把門口 QR（`/campaigns/{id}/qr.svg`）印出來貼好

---

## 9. 尚未涵蓋 / 建議下一步（誠實列出）

| 項目 | 現況 | 建議 |
|---|---|---|
| LINE 真實推播 | Stub，需 token | 申請 LINE 官方帳號 → 填 token，零改碼 |
| 多分店資料隔離 | 單租戶 MVP | 開 RLS（屬 D2 決策，schema 已預留） |
| 正式品牌前端 | 自包含 demo 頁 | 沿用或換品牌版，API 不動 |
| 員工分權帳號 | 單一共享通行碼 | 若要「誰核銷了哪張」可升級為員工帳號（目前稽核記到 actor 欄位但共用身分） |
| 清空配額後改回無限 | 後端 patch 忽略 null | 如需「把配額改回無限」可加一個明確清除旗標（小工） |

---

## 10. 檔案地圖

| 功能 | 檔案 |
|---|---|
| 後台登入/權限 | `restaurant_api/api/auth.py` |
| 活動/獎項/抽獎/券/成效/CSV 服務 | `restaurant_api/services/campaigns_service.py` |
| API 路由 | `restaurant_api/routers/campaigns.py` |
| 資料模型（4 表） | `restaurant_api/models/campaigns.py` |
| 顧客輪盤頁 | `restaurant_api/static/index.html` |
| 店長後台頁 | `restaurant_api/static/admin.html` |
| 夜間過期 job | `restaurant_api/jobs/campaign_expiry.py` |
| 一鍵示範 | `scripts/seed_wheel_campaign.py`（`make wheel-demo`） |
| 設計文件 | `docs/12_launch_wheel_campaign.md` |
| 交接 + 上線 | `COMMANDER_HANDOFF.md` |
| 測試 | `tests/routers/test_campaigns_router.py`、`tests/routers/test_admin_auth.py`、`tests/jobs/test_campaign_expiry.py` |

---

## 11. 驗收建議（你可以自己跑一次）

```bash
make wheel-demo && make api
# 顧客頁：手機開 http://<你的網址>/demo/?campaign=<id>
# 後台：  電腦開 http://<你的網址>/demo/admin.html  （輸入你設的通行碼）
```
在後台選活動 → 看「活動成效」→ 改一個獎項權重按 💾 → 按「⬇ 匯出券 CSV」→ 用「櫃台核銷」掃一張券碼，即可走完整套營運流程。

---

_本報告由 Claude Code 於 session 產出；所有數據為真實伺服器實測。_
