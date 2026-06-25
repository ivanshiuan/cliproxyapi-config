# Spec: Employee Pet Gamification — Models + Migration

> **Module name:** `restaurant_api.models.gamification`
> **Owner domain:** HR / Employee Engagement（養成遊戲化）
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** 一個新 ORM 模組 `restaurant_api/models/gamification.py`（7 表）+ 一份 Alembic 遷移
> **PRD source of truth:** `docs/18_employee_pet_gamification.md`（附錄 B 為本 spec 的設計依據；本 spec 是其精確化）
> **這是 Phase A 的契約基石** — `employee_pet_service` / `employee_reward_service` / `employee_egg_settlement_job` / `employee_pets` router 全部引用此處的表名與欄位名，命名以本 spec 為準。

---

## Background

把員工的考勤／任務／學習換皮成「養一隻電子雞」。員工領蛋（white/silver/gold）餵雞、雞升級
（小雞→母雞→大雞→帝王雞）、達里程碑換真實獎金（正向加碼、需審核、有月池上限）。

資料模型直接沿用專案既有的 **ledger 模式**（見 `models/customers.py::CustomerPointsLedger`、
`models/inventory.py::StockMovement`）：蛋的 SSOT 是 append-only 帳本，餘額是快取、可重算。
真實獎金走有狀態的兌換工作流（pending→approved→paid），月池滿則排隊次月。

**核心合規**（呼應 PRD D1）：紅蛋／生病／活力衰退**只影響遊戲內**數值，**絕不**連結真實薪資。
真錢只從 `employee_reward_pool` 正向發放。

---

## Conventions（硬性，與既有 models 一致）

- 每張表 PK：`id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)`。
- 所有表用 `TenantScopedMixin`（`tenant_id` FK→tenants RESTRICT, indexed）。
- 金錢欄位一律 `Money`（= `Numeric(14,4)`，from `models/base.py`）；**永不** float。
- **蛋的數量是整數**（白/銀/金/紅顆數），用 `Integer`，不是 Money。
- Timestamp 全 tz-aware（`DateTime(timezone=True)`）。
- 帳本/事件表（append-only）：只給 `created_at`（server_default `func.now()`），**不**加 `updated_at`、**不**用 `TimestampedMixin`。
- 有狀態/可更新的表（pet、task 定義、completion、pool、redemption）：用 `TimestampedMixin`。
- 列舉值：跟 `models/orders.py::PaymentMethod` 慣例一致 —— 用 SQLAlchemy `Enum`（native PG enum，給明確 `name=`）**或** `String(N)` + CHECK；architect 擇一但值域不得更動。
- FK 一律 `ondelete="RESTRICT"`（不允許 cascade 抹帳）。
- 全部 model 在 `restaurant_api/models/__init__.py` 註冊匯出（跟既有表一樣）。

---

## 值域（closed sets，不得更動語意）

| Enum | 值 | 說明 |
|---|---|---|
| `PetStage` | `chick` / `hen` / `big` / `emperor` | 小雞 / 母雞 / 大雞 / 帝王雞 |
| `EggType` | `white` / `silver` / `gold` / `red` | 白 / 銀 / 金 / 紅蛋 |
| `CareEventType` | `feed_egg` / `feed_food` / `heal` / `decay` | 餵蛋升級 / 餵飼料維持 / 治療 / 衰退 |
| `TaskCategory` | `attendance` / `learning` / `duty` / `performance` / `course` | 任務類別 |
| `TaskRecurrence` | `once` / `daily` / `weekly` | 重複週期 |
| `CompletionStatus` | `submitted` / `approved` / `rejected` | 任務完成審核狀態 |
| `RedemptionType` | `gold_egg` / `emperor` / `monthly_goal` | 兌換種類 |
| `RedemptionStatus` | `pending` / `approved` / `paid` / `rejected` / `queued` | 兌換工作流狀態（`queued`=月池滿、排隊次月） |

---

## 表 1 — `employee_pets`（每員工一隻雞 · 有狀態 · 餘額為快取）

`TenantScopedMixin` + `TimestampedMixin`。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `employee_id` | UUID FK→employees.id RESTRICT | no | — | indexed |
| `store_id` | UUID FK→stores.id RESTRICT | no | — | 排行榜/池 scope |
| `name` | String(50) | no | — | 雞的名字（員工自取） |
| `stage` | `PetStage` | no | `chick` | 進化階段 |
| `level` | Integer | no | 1 | 等級 |
| `health` | Integer | no | 100 | 0–100，CHECK 0..100 |
| `vitality` | Integer | no | 100 | 0–100，CHECK 0..100 |
| `nest_level` | Integer | no | 0 | 雞窩等級 |
| `feeding_streak_days` | Integer | no | 0 | 連續餵養天數 |
| `last_fed_at` | timestamptz | yes | NULL | 最後餵食 |
| `white_balance` | Integer | no | 0 | 快取：白蛋餘額（SSOT=帳本） |
| `silver_balance` | Integer | no | 0 | 快取：銀蛋餘額 |
| `gold_balance` | Integer | no | 0 | 快取：金蛋餘額 |
| `red_count` | Integer | no | 0 | 快取：紅蛋累計（debuff） |
| `feed_balance` | Integer | no | 0 | 快取：淨化飼料數 |

**約束**
- Unique `(tenant_id, employee_id)` — 一員工一雞。
- CHECK `health BETWEEN 0 AND 100`、`vitality BETWEEN 0 AND 100`。
- Index `(store_id, stage)`、`(store_id, level)`（排行榜用）。
- 餘額欄位是**快取**：真值由 `employee_egg_ledger` 加總，service 層維護一致（見 `employee_pet_service` spec）。

---

## 表 2 — `employee_egg_ledger`（蛋帳本 · append-only · SSOT）

`TenantScopedMixin` only（**不**加 TimestampedMixin；只有 `created_at`）。
**完全比照 `CustomerPointsLedger`** 的設計與不變式。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `employee_id` | UUID FK→employees.id RESTRICT | no | — | indexed |
| `egg_type` | `EggType` | no | — | 哪種蛋 |
| `delta` | Integer | no | — | **signed**：+ 發放 / − 消耗 |
| `reason` | String(64) | no | — | dotted namespace（見下） |
| `business_date` | Date | yes | NULL | 考勤類發蛋的歸屬日（冪等用） |
| `source_ref` | String(128) | yes | NULL | 來源參照（task_completion id / redemption id / 對手 ledger id…） |
| `note` | Text | yes | NULL | 備註 |
| `created_at` | timestamptz | no | func.now() | append-only |

**`reason` dotted namespace**（closed 慣例，不是 enum 但值域固定）：
`attendance.ontime`、`task.complete`、`learning.upload`、`streak.bonus`、
`exchange.up`、`exchange.down`、`redeem.cash`、`redeem.refund`、`penalty.redegg`、`manual.adjust`。

**約束（不變式）**
- **Append-only**：遷移中加 PostgreSQL RULE 擋 UPDATE/DELETE（比照其他 ledger，見 `docs/04_data_schema.md`）。修正一律寫反向 row。
- **冪等 partial unique index** `uq_egg_attendance_once`：`UNIQUE (employee_id, business_date) WHERE reason = 'attendance.ontime'`
  —— 同員工同日只發一次考勤蛋（比照 `uq_points_welcome_once` 的 partial-unique 手法，用 `postgresql_where=text(...)`）。
- Index `(employee_id, created_at)`（時間軸）、`(reason)`。

---

## 表 3 — `employee_pet_care_events`（餵食/照護事件 · append-only）

`TenantScopedMixin` only（只有 `created_at`）。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `employee_id` | UUID FK→employees.id RESTRICT | no | — | indexed |
| `pet_id` | UUID FK→employee_pets.id RESTRICT | no | — | indexed |
| `event_type` | `CareEventType` | no | — | feed_egg/feed_food/heal/decay |
| `egg_type` | `EggType` | yes | NULL | 餵食時用了哪種蛋（decay 時 NULL） |
| `amount` | Integer | no | 0 | 消耗數量 |
| `health_delta` | Integer | no | 0 | 對健康的影響（可正可負） |
| `vitality_delta` | Integer | no | 0 | 對活力的影響 |
| `note` | Text | yes | NULL | |
| `created_at` | timestamptz | no | func.now() | append-only |

Index `(pet_id, created_at)`。

---

## 表 4 — `employee_tasks`（任務定義 · 有狀態 · 可軟刪）

`TenantScopedMixin` + `TimestampedMixin` + `SoftDeleteMixin`。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `store_id` | UUID FK→stores.id RESTRICT | yes | NULL | NULL=全租戶通用 |
| `title` | String(120) | no | — | 任務名稱 |
| `description` | Text | yes | NULL | |
| `category` | `TaskCategory` | no | — | attendance/learning/duty/performance/course |
| `egg_type` | `EggType` | no | `white` | 完成發哪種蛋 |
| `egg_qty` | Integer | no | 1 | 發幾顆（>0，CHECK） |
| `recurrence` | `TaskRecurrence` | no | `once` | once/daily/weekly |
| `requires_evidence` | Boolean | no | false | 是否需上傳佐證 |
| `requires_approval` | Boolean | no | false | 是否需主管核可才入帳 |
| `is_active` | Boolean | no | true | |

CHECK `egg_qty > 0`。Index `(tenant_id, store_id, is_active)`。

---

## 表 5 — `employee_task_completions`（任務完成 · 有狀態審核流）

`TenantScopedMixin` + `TimestampedMixin`。**不是** append-only（status 會更新）。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `employee_id` | UUID FK→employees.id RESTRICT | no | — | indexed |
| `task_id` | UUID FK→employee_tasks.id RESTRICT | no | — | indexed |
| `business_date` | Date | no | — | 歸屬日 |
| `evidence_url` | String(512) | yes | NULL | 佐證（截圖/表單連結） |
| `status` | `CompletionStatus` | no | `submitted` | submitted/approved/rejected |
| `reviewed_by` | UUID FK→employees.id RESTRICT | yes | NULL | 審核者 |
| `reviewed_at` | timestamptz | yes | NULL | |
| `egg_granted` | Boolean | no | false | 是否已寫蛋帳本（防重複發） |
| `note` | Text | yes | NULL | |

**冪等 partial unique** `uq_task_completion_daily`：
`UNIQUE (employee_id, task_id, business_date) WHERE status <> 'rejected'`
—— 同員工同任務同日不可重複領（被拒的不佔位、可重交）。
Index `(task_id, status)`。

---

## 表 6 — `employee_reward_pool`（獎勵池設定 · 每店一筆當期設定）

`TenantScopedMixin` + `TimestampedMixin`。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `store_id` | UUID FK→stores.id RESTRICT | no | — | unique（一店一筆） |
| `white_per_silver` | Integer | no | 10 | 兌換率：白→銀 |
| `silver_per_gold` | Integer | no | 10 | 兌換率：銀→金 |
| `gold_egg_cash_value` | Money | no | 500 | 1 金蛋兌真錢 |
| `emperor_bonus` | Money | no | 5000 | 帝王雞獎金（PRD D7） |
| `monthly_goal_bonus` | Money | no | 0 | 月目標獎金（可選） |
| `monthly_pool_budget` | Money | no | 6000 | 月真錢上限（PRD D6） |
| `monthly_pool_spent` | Money | no | 0 | 當期已花（月初 job 重置） |
| `pool_period` | Date | no | — | 當期月份（該月 1 號），`monthly_pool_spent` 所屬期間 |

Unique `(store_id)`。CHECK 各率/金額 `>= 0`。

> 預設值即 PRD 拍板：`monthly_pool_budget=6000`、`emperor_bonus=5000`、`gold_egg_cash_value=500`、兌換率 10/10。

---

## 表 7 — `employee_reward_redemptions`（真錢兌換 · 有狀態工作流）

`TenantScopedMixin` + `TimestampedMixin`。

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | uuid7 | PK |
| `employee_id` | UUID FK→employees.id RESTRICT | no | — | indexed |
| `store_id` | UUID FK→stores.id RESTRICT | no | — | indexed |
| `redemption_type` | `RedemptionType` | no | — | gold_egg/emperor/monthly_goal |
| `eggs_spent` | Integer | no | 0 | 扣了幾顆（對應已寫的 `redeem.cash` 帳本） |
| `cash_amount` | Money | no | — | 真錢金額（>0，CHECK） |
| `status` | `RedemptionStatus` | no | `pending` | pending/approved/paid/rejected/queued |
| `queued_for` | Date | yes | NULL | 月池滿時排到哪個月（該月 1 號） |
| `requested_at` | timestamptz | no | func.now() | |
| `reviewed_by` | UUID FK→employees.id RESTRICT | yes | NULL | |
| `reviewed_at` | timestamptz | yes | NULL | |
| `paid_at` | timestamptz | yes | NULL | |
| `note` | Text | yes | NULL | |

CHECK `cash_amount > 0`。Index `(store_id, status)`、`(status, queued_for)`（排隊掃描用）。

---

## Alembic Migration

- 一份新遷移，`down_revision` 接目前 head（implementer 跑 `alembic heads` 確認）。
- 建 8 個 native PG enum type（若採 Enum 路線）+ 7 張表 + 全部 index/constraint。
- **Append-only RULE**（對 `employee_egg_ledger` 與 `employee_pet_care_events`）：
  比照既有 ledger 遷移，`op.execute` 建立 `RULE ... AS ON UPDATE/DELETE ... DO INSTEAD NOTHING`（或專案既有慣例的 raise）。**下行 `downgrade()` 要 drop rule、drop table、drop enum type**。
- 遷移要能 `alembic upgrade head` 與 `downgrade -1` 來回乾淨（`make full-check` 的 alembic gate）。

---

## Acceptance Criteria

> 對應測試放 `tests/test_gamification_models.py`，命名 `test_models_ac_NN_*`。用 `conftest.py` 的 async fixtures，每測一個 SAVEPOINT、scope 到 `seed_tenant`/`seed_store`。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | 建立 pet | 插入一筆 `employee_pets` → 預設 `stage='chick'`、`level=1`、`health=100`、`vitality=100`、所有蛋餘額=0。 |
| AC-2 | 一員工一雞 | 同 `(tenant_id, employee_id)` 第二筆 pet → IntegrityError（unique）。 |
| AC-3 | health 邊界 | 插入 `health=101` 或 `-1` → IntegrityError（CHECK）。 |
| AC-4 | 蛋帳本 append-only：UPDATE 被擋 | 寫一筆 ledger 後 UPDATE 它的 `delta` → 不生效/被擋（依 RULE 行為驗證）。 |
| AC-5 | 蛋帳本 append-only：DELETE 被擋 | DELETE 一筆 ledger row → 不生效/被擋。 |
| AC-6 | 考勤蛋冪等 | 同 `(employee_id, business_date)` 兩筆 `reason='attendance.ontime'` → 第二筆 IntegrityError。 |
| AC-7 | 非考勤 reason 不受冪等限制 | 同員工同日多筆 `reason='task.complete'` → 全部成功（partial index 只管 attendance.ontime）。 |
| AC-8 | signed delta | 可寫 `delta=+1`（grant）與 `delta=-10`（exchange.down）兩種 row。 |
| AC-9 | 任務完成日冪等 | 同 `(employee_id, task_id, business_date)` 且 status≠rejected 兩筆 → 第二筆 IntegrityError；但若第一筆 status=rejected 則第二筆可成功。 |
| AC-10 | task egg_qty 正數 | `egg_qty=0` → IntegrityError（CHECK）。 |
| AC-11 | pool 預設值 | 插入只給 `store_id`+`pool_period` 的 pool → `monthly_pool_budget=6000`、`emperor_bonus=5000`、`gold_egg_cash_value=500`、`white_per_silver=10`、`silver_per_gold=10`、`monthly_pool_spent=0`。 |
| AC-12 | 一店一池 | 同 `store_id` 第二筆 pool → IntegrityError（unique）。 |
| AC-13 | redemption 預設 status | 插入 redemption 只給必填 → `status='pending'`、`eggs_spent=0`、`queued_for=NULL`。 |
| AC-14 | cash_amount 正數 | redemption `cash_amount=0` → IntegrityError（CHECK）。 |
| AC-15 | Money 精度 | `gold_egg_cash_value=Decimal("500.0000")` 寫入後讀出仍為 4dp Decimal、非 float。 |
| AC-16 | tenant scope | 全 7 表都有 `tenant_id` 且 NOT NULL；插入缺 tenant → IntegrityError。 |

---

## Out of scope（本 spec 只做 schema 層，避免 drift）

- 任何業務邏輯（發蛋、餵食、升級、兌換、排隊）→ 在 `employee_pet_service` / `employee_reward_service` spec。
- 每日結算 job → `employee_egg_settlement_job` spec。
- HTTP / FastAPI router / Pydantic schema → `employee_pets` router spec。
- 餘額重算演算法的實作 → service spec（本 spec 只定義快取欄位存在）。
- 顧客雙邊表（Phase 2）。
- 同儕互送蛋（Phase 2，D8）。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `models/base.py` | `uuid7`、`Money`、三個 Mixin |
| `models/customers.py::CustomerPointsLedger` | **蛋帳本的範本**（append-only + partial unique 冪等） |
| `models/inventory.py::StockMovement` | append-only ledger + reversal 慣例參考 |
| `models/employees.py` / `models/stores.py` | FK 目標 |
| `employee_pet_service`（下一支 spec） | 讀寫本層 7 表，維護快取餘額一致 |
| `restaurant_api/models/__init__.py` | 註冊匯出全部 7 model |
| `restaurant_api/alembic/` | 新遷移落地 schema |

— end of spec —
</content>
