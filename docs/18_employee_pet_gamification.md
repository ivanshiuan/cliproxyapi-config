# 18 — 員工電子雞養成（考勤／任務／學習 遊戲化）

> 把員工的考勤、任務、學習、考核，換皮成「養一隻電子雞」。
> 員工登入自己的畫面看到自己的雞，每天餵食、完成任務領蛋、把雞養大。
> **核心理念（Ivan 拍板）**：真實金錢**只做正向加碼**，從老闆設定的固定獎勵池發放；
> 紅蛋／生病／沒活力**只影響遊戲內**健康值與升級速度，**不扣真實薪資**（避開勞基法工資爭議）。
>
> 本文是設計稿，待 Ivan 審核規則後再寫程式。實作前不動任何 schema。

---

## 〇、一句話總結與為什麼這樣設計

驅動方式從「每天念員工」改成「每天養自己的雞」。完成考勤／任務／學習 → 領蛋 → 餵雞 → 雞升級 → 達到里程碑領真實獎金。

**為什麼這套幾乎不用從零造**：顧客端早就有成熟的遊戲化骨架，員工版只是換皮 + 換資料來源：

| 員工版概念 | 直接複用的既有模式 | 檔案 |
|---|---|---|
| 蛋帳本（append-only、可稽核、餘額由帳本重算） | `customer_points_ledger`（signed delta + dotted reason + recompute balance） | `models/customers.py:160` |
| 兌換真錢／獎品 + 審核 | `campaigns`（marketing_campaigns / campaign_prizes / campaign_spins / campaign_vouchers） | `models/campaigns.py` |
| 發蛋的資料來源（準時上下班） | `time_clocks`（已預分桶工時）、`shifts`（排班 ground truth）、`leave_requests` | `models/hr.py` |
| 員工主檔 | `employees` | `models/employees.py:39` |
| 每日結算 job | `jobs/`（expiry / points / COGS 已有同型背景任務） | `restaurant_api/jobs/` |
| 寫稽核 | `services/audit_service.audit()` | 法則：不可直接 INSERT AuditLog |

---

## 一、蛋經濟（Egg Economy）

### 蛋種

| 蛋 | 性質 | 取得 | 價值定位 |
|---|---|---|---|
| 🥚 白蛋 white | 正向・基礎 | 完成一件任務／當日準時考勤／學習上傳 | 最小單位 |
| 🥈 銀蛋 silver | 正向・進階 | 10 顆白蛋兌換升級 | 中階里程碑 |
| 🥇 金蛋 gold | 正向・高價值 | 10 顆銀蛋兌換升級 | **可兌真錢**（值約 NT$500，由獎勵池設定） |
| ❤️ 紅蛋 red | **負向・debuff** | 做錯事／曠職／主管或同儕標記 | **不換錢**，只讓雞生病 |

### 兌換階梯（升級制）

```
10 白蛋  ─┐
          ├─►  1 銀蛋
10 銀蛋  ─┘
          ├─►  1 金蛋
1 金蛋   ──►  NT$500（需走兌換審核，見第六節）
```

兌換比率（`10:1`、金蛋現值）全部存在 `employee_reward_pool`，老闆可調，**不寫死在程式**。

### 怎麼賺蛋（每一件「做到」= 一顆白蛋）

- **考勤**：當日有打卡、無遲到、無早退 → 1 白蛋（每日上限 1，由每日 job 結算）
- **任務**：完成任一指派任務（出勤前準備、清潔、盤點…）→ 1 白蛋
- **學習**：上傳學習資料／完成線上課程章節 → 1 白蛋
- **連續**：連續餵養／連續達標另有 streak 加碼（見第三節）

> 鐵律：發蛋一律寫進 `employee_egg_ledger`（append-only），雞身上的餘額由帳本重算，
> 跟顧客點數一樣「never UPDATE / never DELETE，修正用反向 row」。

---

## 二、雞的進化（Chicken Progression）

```
🐣 小雞 chick  ──►  🐔 母雞 hen  ──►  🦃 大雞 rooster  ──►  👑 帝王雞 emperor
```

| 階段 | enum | 升級條件（建議初值，可調） | 里程碑獎勵 |
|---|---|---|---|
| 小雞 | `chick` | 起始 | — |
| 母雞 | `hen` | 累積 10 白蛋 等值 + 連續餵養 ≥ 7 天 | 解鎖蓋雞窩 |
| 大雞 | `rooster` | 累積 1 金蛋 等值 + 健康 ≥ 70 | 進階外觀 |
| 帝王雞 | `emperor` | 累積 3 金蛋 等值 + 當月全勤 | **真實獎金 NT$3,000–5,000**（獎勵池設定，需審核） |

升級條件全部存 `employee_reward_pool` / 設定表，**不寫死**。雞窩 `nest_level`、房子是長期養成的視覺成就（提升升級速度的小 buff）。

---

## 三、餵食與照護迴圈（Daily Loop = 日活誘因）

- **每天餵食**：登入畫面點「餵雞」。可以用蛋餵，也可以用飼料餵，效果不同：
  - 用蛋餵 → 直接推進升級（消耗蛋）
  - 用飼料餵 → 維持健康／活力（不消耗蛋）
- **連續餵養 streak**：每連續 3 天餵養 → 額外得「淨化飼料」（高級飼料，健康回復更多）。中斷歸零。
- **飼料 = 養分**：可買養分讓雞更健康，或蓋雞窩／房子做長期養成。
- 所有餵食／照護動作寫進 `employee_pet_care_events`（append-only），健康值由事件重算。

---

## 四、健康與生病（**只在遊戲內**，不碰真錢）

員工問的「紅蛋讓雞生病、要補很多金雞蛋才補得回來、同儕可送紅蛋」這段，**全部留在遊戲層**：

- 雞有 `health`（0–100）與 `vitality`（活力）兩個遊戲內數值。
- **扣健康**：收到紅蛋 / 連續曠職 / 做錯事被標記 → 扣 health。
- **生病表現**：health 低 → 雞外觀變憔悴、活力低、**升級速度變慢**（這就是懲罰，驅動力還在）。
- **補救**：餵金蛋／淨化飼料回復健康。**只消耗遊戲內的蛋與飼料，不換算、不扣真實薪資。**
- **同儕／主管送紅蛋**：先做成「需主管核准才生效」的標記，避免私下互整變成勞資糾紛（見第七節）。

> 與最初構想的差異：構想裡「補金雞蛋換算獎金價值」被改成「補金雞蛋＝遊戲內資源」。
> 真錢只在第六節的正向兌換出現。這是 Ivan 選的「正向發錢＋懲罰只在遊戲內」路線。

---

## 五、資料表設計（schema-only，沿 docs/04 體例）

> 全部遵守專案法則：UUIDv7 主鍵、`tenant_id` / `created_at` / `updated_at`、
> 金錢用 `Money = Numeric(14,4)`、帳本表 append-only（DB RULE 擋 UPDATE/DELETE）、蛋數量用整數。

### 1. `employee_pets` — 一名員工一隻雞（快照，餘額可由帳本重算）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | uuid (v7) | PK |
| `tenant_id` | uuid | 租戶 |
| `employee_id` | uuid FK→employees | **unique**（一人一隻雞） |
| `name` | text | 員工自取的雞名 |
| `stage` | enum chick/hen/rooster/emperor | 進化階段 |
| `health` | int 0–100 | 健康值（由 care_events 重算） |
| `vitality` | int 0–100 | 活力 |
| `level` | int | 數值等級 |
| `nest_level` | int | 雞窩／房子等級 |
| `white_balance` / `silver_balance` / `gold_balance` | int | 蛋餘額快照（SSOT 是帳本） |
| `red_count` | int | 紅蛋累計（debuff 計數） |
| `feed_balance` | int | 飼料餘額 |
| `feeding_streak_days` | int | 連續餵養天數 |
| `last_fed_at` | timestamptz | 上次餵食 |

### 2. `employee_egg_ledger` — 蛋帳本（**append-only，SSOT**，仿 customer_points_ledger）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | uuid (v7) | PK |
| `tenant_id` | uuid | 租戶 |
| `employee_id` | uuid FK | 索引 |
| `egg_type` | enum white/silver/gold/red | 蛋種 |
| `delta` | int (signed) | +發放 / −消耗或兌換 |
| `reason` | varchar(64) | dotted namespace：`attendance.ontime`、`task.complete`、`learning.upload`、`streak.bonus`、`exchange.up`（10換1）、`exchange.down`、`redeem.cash`、`penalty.redegg`、`manual.adjust` |
| `source_ref` | uuid null | 來源（task_completion_id / time_clock_id） |
| `note` | text null | 備註 |
| `created_at` | timestamptz | |

DB-level idempotency（仿顧客點數的 partial unique index）：`attendance.ontime` 對 `(employee_id, 日期)` 唯一 → **同一天準時只發一次**，多 instance / job 重跑也不重複發。

### 3. `employee_pet_care_events` — 餵食／照護事件（append-only）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `tenant_id` / `created_at` | | 標配 |
| `pet_id` | uuid FK→employee_pets | |
| `event_type` | enum feed_egg/feed_pellet/build_nest/heal/streak_bonus/sick | |
| `egg_type` | enum null | 用蛋餵時記哪種蛋 |
| `amount` | int | 消耗數量 |
| `health_delta` | int (signed) | 對健康的影響 |
| `note` | text null | |

### 4. `employee_tasks` — 任務定義（什麼能領蛋）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `tenant_id` / `store_id` / `created_at` / `updated_at` | | 標配 |
| `title` / `description` | text | |
| `category` | enum attendance/learning/duty/performance/course | 把考核也綁進來 |
| `egg_type` / `egg_qty` | enum / int | 完成發什麼蛋、幾顆 |
| `recurrence` | enum once/daily/monthly | 出功課用 |
| `requires_evidence` | bool | 是否需上傳資料才算 |
| `requires_approval` | bool | 是否需主管核准才發蛋 |
| `is_active` | bool | |

### 5. `employee_task_completions` — 任務完成（append-only）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `tenant_id` / `created_at` | | 標配 |
| `task_id` | uuid FK | |
| `employee_id` | uuid FK | |
| `evidence_url` | text null | 上傳的學習／完成證明 |
| `status` | enum submitted/approved/rejected | |
| `reviewed_by` | uuid null FK→employees | |
| `reviewed_at` | timestamptz null | |
| `egg_granted` | bool | 是否已發蛋（連到 ledger，防重發） |

### 6. `employee_reward_pool` — 老闆設定的獎勵池與兌換率（一租戶一筆設定）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `tenant_id` / `updated_at` | | |
| `white_per_silver` / `silver_per_gold` | int | 兌換階梯（預設 10 / 10） |
| `gold_egg_cash_value` | Money | 金蛋現值（預設 500） |
| `emperor_bonus` | Money | 帝王雞獎金（3,000–5,000） |
| `monthly_goal_bonus` | Money | 當月達標獎金（1,000） |
| `monthly_pool_budget` | Money | **每月獎勵池上限**（風控：發超過就擋／轉人工） |
| `monthly_pool_spent` | Money | 本月已發（重算自 redemptions） |

### 7. `employee_reward_redemptions` — 真錢兌換（**需人工審核**）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` / `tenant_id` / `created_at` | | |
| `employee_id` | uuid FK | |
| `redemption_type` | enum gold_egg/emperor/monthly_goal | |
| `eggs_spent` | int | 消耗蛋數（連到 ledger 的 `redeem.cash`） |
| `cash_amount` | Money | 對應現金 |
| `status` | enum pending/approved/paid/rejected | |
| `requested_at` / `approved_by` / `approved_at` / `paid_at` | | 審核軌跡 |

---

## 六、真實金錢流（只正向、需審核、有預算上限）

```
員工累積到 1 金蛋 / 帝王雞 / 當月達標
        │
        ▼
建立 reward_redemption (status=pending)   ← 消耗蛋寫 ledger: reason=redeem.cash
        │
        ▼
老闆 / 店長在後台審核  ──► approved ──► 出納發放 ──► paid
        │                                    │
        └──► rejected（蛋退回，寫反向 ledger row）
                                             ▼
                            每筆都走 audit_service.audit() 留稽核
```

風控三道閘：
1. **只正向**：兌換金額永遠 ≥ 0，系統沒有「扣真錢」的路徑。
2. **預算上限**：`monthly_pool_budget` 擋住超發；接近上限轉人工。
3. **人工審核**：真錢一律 pending → approved，不自動出款。

---

## 七、勞基法／合規注意（呼應 docs/08）

| 風險點 | 處理 |
|---|---|
| 工資不得任意扣減（勞基法 §22、§26） | 遊戲內懲罰**不碰薪資**；紅蛋只扣遊戲健康值 |
| 同儕互送懲罰易生霸凌／勞資糾紛 | 送紅蛋**需主管核准才生效**，且全程 `audit_log` 可追 |
| 獎金變相成「應得工資」的爭議 | 文件明定為**恩惠性／激勵性獎金**，發放條件公開、由獎勵池支應 |
| 個資（學習上傳、考核） | 沿用既有 tenant 隔離 + 軟刪除；上傳檔走既有 asset 流程 |

> 實作前建議讓 `restaurant-domain-expert` agent 再過一次第六、七節。

---

## 八、和既有資料的觸發點（每日 job）

仿 `jobs/`（expiry / points / COGS）新增一支每日結算 job：

```python
# 每日 02:00 跑（台北時間），結算前一日
for emp in active_employees(tenant):
    tc = time_clocks_for(emp, yesterday)
    if tc and not late(tc) and not early_leave(tc):
        grant_egg(emp, white=1, reason="attendance.ontime", source_ref=tc.id)
        # partial unique index 保證同日只發一次
    if absent(emp, yesterday):           # 排班有、打卡無、且非請假
        add_red_egg(emp, reason="penalty.absence")   # 只扣遊戲健康
    decay_health_if_unfed(emp)           # 久未餵食活力下降
```

考勤判定（遲到／早退）直接讀 `shifts.scheduled_start/end` vs `time_clocks.clock_in/out`，不重造輪子。

---

## 九、員工自己的畫面（前端，Phase 2）

登入後一頁：

```
┌─────────────────────────────┐
│   🐔 「咕咕」 Lv.7  母雞       │   ← 雞的圖 + 名字 + 階段
│   健康 ███████░░ 78          │
│   活力 ████████░ 85          │
│   連續餵養 🔥 5 天            │
├─────────────────────────────┤
│  🥚×7  🥈×2  🥇×1  ❤️×0      │   ← 蛋包
│  [ 餵蛋 ]  [ 餵飼料 ]  [ 兌換 ] │
├─────────────────────────────┤
│  今日任務                     │
│  ☑ 準時打卡        +🥚        │
│  ☐ 完成清潔SOP      +🥚  [上傳] │
│  ☐ 線上課程：食安   +🥚  [去上] │
├─────────────────────────────┤
│  排行榜：本店帝王雞 👑 ×2      │
└─────────────────────────────┘
```

---

## 十、Phase 切分與待做

**Phase A（先做，本次若 Ivan 點頭）**：核心後端
- 7 張表 + Alembic migration（蛋帳本 append-only RULE）
- `egg_service`（發蛋／兌換階梯／餘額重算）、`pet_service`（餵食／健康重算）
- 每日結算 job（考勤發蛋）
- router：`/pets/me`、`/pets/me/feed`、`/eggs/exchange`、`/tasks`、`/tasks/{id}/complete`
- 真錢兌換 router + 審核流（pending→approved→paid）+ audit
- pytest 整合測（沿 conftest 的 savepoint fixture）

**Phase B**：員工前端畫面、排行榜、線上課程整合
**Phase C**：考核綁定、自訂任務模板、推播（LINE）提醒餵雞

---

## 十一、留給 Ivan 的決策（實作前再確認）

1. **發蛋顆粒度**：一件任務固定 1 白蛋，還是不同任務不同蛋／不同顆數？（目前設計支援「每任務自訂」）
2. **金蛋現值**：NT$500 是預設值，要不要分店／分職級不同？
3. **帝王雞門檻**：3 金蛋 + 全勤是建議值，會不會太難／太易？
4. **紅蛋來源**：只限主管核准，還是開放同儕送（需核准）？預設「需核准」。
5. **獎勵池上限**：每月每店預算抓多少？（風控用）
