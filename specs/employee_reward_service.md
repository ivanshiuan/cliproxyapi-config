# Spec: Employee Reward Service — Real-Cash Redemption Workflow

> **Module name:** `restaurant_api.services.employee_reward_service`
> **Owner domain:** HR / Employee Engagement（養成遊戲化 · 真錢兌換）
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** 一個新 service 模組 `restaurant_api/services/employee_reward_service.py` + 一份測試 `tests/test_employee_reward_service.py`
> **依賴契約（SSOT）:** `specs/employee_pet_models.md`（表 / 欄位 / enum 以該 spec 為準，不得偏移）
> **PRD source of truth:** `docs/18_employee_pet_gamification.md`（§5.2 防通膨三閘、§5.4、D6/D7、附錄 B 月池排隊規則）

---

## Background

PRD D1 鐵律：真實金錢**只做正向加碼**，懲罰只在遊戲內。員工把蛋（軟貨幣）累積到金蛋 /
帝王雞門檻後，發起「兌換真錢」工作流。蛋是 append-only 帳本（`employee_egg_ledger`）的
SSOT；真錢兌現走有狀態的審核流（`employee_reward_redemptions`），並受每店每月硬上限
（`employee_reward_pool.monthly_pool_budget`，D6 = NT$6,000）約束。

本 service 是這條真錢流的**業務邏輯層**：發起兌換扣蛋、月池閘門判定、人工審核核可、出納
標記已付、駁回退蛋、月初排隊轉正。它體現 §5.2 的防通膨三閘：**(1) 只正向發放、(2) 月池
硬上限、(3) 人工審核**。因月池 6,000、帝王獎金 5,000（D7），同月只容得下 1 隻帝王雞，
第 2 隻起 FIFO 排隊至次月——這個稀缺是刻意設計。

本 service 是 async（吃 `session: AsyncSession`）、**不 commit**（交易邊界在 DI 層
`api/deps.py::get_db`，與專案所有 service 一致）。所有兌換 / 核可 / 退蛋 / 排隊轉正
都走 `services/audit_service.audit()`。Domain 錯誤一律拋 `DomainError` 子類，**不**用
raw `HTTPException`。

---

## Goal

提供一組純業務邏輯的 async 函式，操作 `employee_reward_redemptions` /
`employee_reward_pool` / `employee_egg_ledger` 三張表，實作真錢兌換的完整狀態機與
月池排隊演算法；金錢全程 `Decimal`、蛋數量 `int`、永不 `float`；每個寫入路徑落稽核；
任何路徑都不可產生「扣員工真實薪資」的效果。

---

## Scope

### In scope

- `request_redemption(...)`：員工發起兌換（gold_egg / emperor / monthly_goal），驗蛋餘額足夠 → 寫 `employee_egg_ledger` 的 `redeem.cash`（signed delta < 0）扣蛋 → 建 `employee_reward_redemptions`（status=pending），並**立即套月池閘門**判定 pending vs queued。
- 月池閘門（核心）：核可 / 發起前檢查 `monthly_pool_spent + cash_amount <= monthly_pool_budget`；超過 → status=`queued`、`queued_for`=次月 1 號、**蛋不退**（資格保留）。
- `approve_redemption(...)`：pending → approved，累加 `monthly_pool_spent`。
- `mark_paid(...)`：approved → paid，寫 `paid_at`。
- `reject_redemption(...)`：(pending|queued) → rejected，**退蛋**寫反向帳本 `redeem.refund`（signed delta > 0）；若已 approved 過則同步回沖 `monthly_pool_spent`。
- `process_queued_for_period(...)`：月初 job 呼叫的 helper——重置 `monthly_pool_spent=0`、更新 `pool_period`，依 FIFO 掃 `status=queued` 的 redemptions，在新月池容量內依序轉回 pending 直到再次觸頂。
- 嚴格狀態機：非法轉移拋 `ConflictError`。
- 蛋餘額計算：由 `employee_egg_ledger` 加總（SSOT），不可信快取欄位。

### Out of scope（避免 Coder drift）

- 發蛋 / 餵食 / 進化 / 蛋兌換階梯（white→silver→gold）→ `employee_pet_service` spec。
- 考勤結算發蛋、cron 排程本身（每日 02:00、月初觸發）→ `employee_egg_settlement_job` spec。本 service 只提供「可被 job 呼叫」的純邏輯函式 `process_queued_for_period`，不含排程。
- HTTP / FastAPI router / Pydantic 請求回應 schema → `employee_pets` router spec。
- 多幣別（僅 TWD）。
- ORM model / migration（在 `employee_pet_models.md`，本 service 只 import 既有 model）。
- 快取餘額欄位（`employee_pets.gold_balance` 等）的維護一致性 → `employee_pet_service`（本 service 讀帳本算真值，不負責回寫 pet 快取）。

---

## 狀態機（RedemptionStatus）

```
                    request_redemption
                          │
            月池容得下?   │   月池滿?
            ┌─────────────┴─────────────┐
            ▼                            ▼
        [pending] ─approve_redemption→ [approved] ─mark_paid→ [paid]
            │                            │
            │ reject_redemption          │ reject_redemption
            ▼  (+退蛋)                    ▼  (+退蛋 +回沖spent)
        [rejected]                   [rejected]

        [queued] ──process_queued_for_period(次月,容量內)──→ [pending]
            │
            │ reject_redemption (+退蛋)
            ▼
        [rejected]
```

合法轉移（其餘皆拋 `ConflictError`）：
- pending → approved | rejected
- approved → paid | rejected
- queued → pending（僅由 `process_queued_for_period`）| rejected
- paid → （終態，無出口）
- rejected → （終態，無出口）

---

## Public interface

> 全部 async；吃 `session` 且 **不 commit**；`tenant_id` / `actor_id` 由 router 從 DI 注入。
> 回傳 ORM `EmployeeRewardRedemption` 實例（router 層自行轉 Pydantic 回應）。

```python
async def request_redemption(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    store_id: uuid.UUID,
    redemption_type: RedemptionType,   # gold_egg / emperor / monthly_goal
    eggs_spent: int,                   # 本次扣的金蛋顆數，> 0；gold_egg 通常 1、emperor 通常 3
    actor_id: uuid.UUID | None = None,
    note: str | None = None,
) -> EmployeeRewardRedemption:
    """員工發起真錢兌換。

    1. 讀 pool（該 store 當期 employee_reward_pool；無則 NotFoundError）。
    2. 依 redemption_type 算 cash_amount：
       gold_egg → eggs_spent * pool.gold_egg_cash_value
       emperor  → pool.emperor_bonus（eggs_spent 為門檻金蛋數，預設 3）
       monthly_goal → pool.monthly_goal_bonus（須 > 0，否則 ValidationError）
    3. 算金蛋餘額（employee_egg_ledger 加總 egg_type='gold' 的 delta）；
       不足 eggs_spent → InsufficientEggsError(=DomainError)。
    4. 寫扣蛋帳本：egg_type='gold', delta = -eggs_spent, reason='redeem.cash'。
    5. 月池閘門：monthly_pool_spent + cash_amount <= monthly_pool_budget ?
       是 → status=pending；否 → status=queued, queued_for=次月1號。
    6. 建 redemption（eggs_spent, cash_amount, status, source_ref=ledger.id 對接）。
    7. audit('reward.requested' 或 'reward.queued')。
    """


async def approve_redemption(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    redemption_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    note: str | None = None,
) -> EmployeeRewardRedemption:
    """pending → approved。再驗一次月池容量（防併發超發）：
    spent + cash_amount > budget → BudgetExceededError。
    成功則 monthly_pool_spent += cash_amount、寫 reviewed_by/at、audit('reward.approved')。
    非 pending → ConflictError。"""


async def mark_paid(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    redemption_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> EmployeeRewardRedemption:
    """approved → paid，寫 paid_at=now()。非 approved → ConflictError。
    audit('reward.paid')。**不**動帳本（蛋早在 request 時已扣）。"""


async def reject_redemption(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    redemption_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    reason: str,
) -> EmployeeRewardRedemption:
    """(pending|queued|approved) → rejected。退蛋：寫反向帳本
    egg_type='gold', delta = +eggs_spent, reason='redeem.refund', source_ref=redemption.id。
    若原本是 approved（已佔月池）→ monthly_pool_spent -= cash_amount（夾下限 0）。
    paid / rejected → ConflictError（不可駁回已付 / 已駁回）。audit('reward.rejected')。"""


async def process_queued_for_period(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    new_period: date,            # 新月份的 1 號
) -> list[EmployeeRewardRedemption]:
    """月初 job helper（純邏輯、無排程）。

    1. 重置 pool：monthly_pool_spent = Decimal("0")、pool_period = new_period。
    2. FIFO 取該 store status='queued' 的 redemptions，依 requested_at ASC 排序。
    3. 逐筆嘗試：running_spent + cash_amount <= budget ?
       是 → status=pending、queued_for=NULL、audit('reward.dequeued')、running_spent += cash_amount。
       否 → 停留 queued、更新 queued_for=再次次月（保持排隊），跳過後續（FIFO 不可越位插隊）。
    4. 回傳本次被轉成 pending 的 redemptions list。
    註：此函式只轉 queued→pending，不自動 approve（人工審核閘仍在）。
    """
```

> 私有 helper（`_` 前綴）：`_load_pool`、`_gold_egg_balance`、`_resolve_cash_amount`、
> `_next_month_first`、`_assert_status`。所有公開函式皆有完整 type hints。

---

## 月池排隊演算法（步驟化）

**發起時（request_redemption）**
1. `budget = pool.monthly_pool_budget`、`spent = pool.monthly_pool_spent`。
2. 若 `spent + cash_amount <= budget` → `status=pending`（仍待人工核可，尚未佔池）。
3. 否則 → `status=queued`、`queued_for=_next_month_first(pool.pool_period)`、蛋已扣不退。

**核可時（approve_redemption）**
4. 重驗 `spent + cash_amount <= budget`（防多筆 pending 併發核可超發）；超過拋 `BudgetExceededError`，**狀態保持 pending**（讓主管改排隊或下月再核）。
5. 通過 → `monthly_pool_spent += cash_amount`（這一步才真正佔池）。

**月初重置（process_queued_for_period）**
6. `monthly_pool_spent = 0`、`pool_period = new_period`。
7. `running = Decimal("0")`；FIFO 掃 queued（`ORDER BY requested_at ASC`）。
8. 每筆：`running + cash_amount <= budget` → 轉 pending、`queued_for=NULL`、`running += cash_amount`；否則保持 queued 並停止（嚴格 FIFO，不跳過大額去塞小額）。

> **數字直覺（D7）**：budget=6000、emperor=5000。同月第 1 隻帝王雞核可後 spent=5000，
> 剩 1000。第 2 隻帝王雞（5000）發起時 5000+5000=10000 > 6000 → 直接 queued。
> 剩餘 1000 仍可容 2 顆金蛋小額（gold_egg=500 各）→ pending。

---

## Acceptance criteria

> 對應測試 `tests/test_employee_reward_service.py`，命名 `test_reward_ac_NN_*`。
> 用 `tests/conftest.py` 的 async fixtures（`AsyncSession`），每測一個 SAVEPOINT、跑完
> rollback；查詢 scope 到 `seed_tenant`/`seed_store`/seed employee；金額一律 `Decimal(...)`。
> 每測自備一筆 `employee_reward_pool`（預設值見 `employee_pet_models.md` AC-11）。

| # | 名稱 | 驗收條件 |
|---|---|---|
| AC-1 | gold_egg happy path | 員工有 5 金蛋餘額，pool 預設（gold_egg_cash_value=500、spent=0）。`request_redemption(type=gold_egg, eggs_spent=1)` → redemption.status='pending'、cash_amount=Decimal("500.0000")、eggs_spent=1；ledger 新增一筆 gold delta=-1 reason='redeem.cash'；金蛋餘額變 4。 |
| AC-2 | 餘額不足拒絕 | 員工金蛋餘額=2，`request_redemption(eggs_spent=3)` → 拋 `InsufficientEggsError`（DomainError 子類）；**未**寫任何 ledger、**未**建 redemption。 |
| AC-3 | emperor happy path | 員工有 3 金蛋，spent=0。`request_redemption(type=emperor, eggs_spent=3)` → status='pending'、cash_amount=Decimal("5000.0000")、扣 3 金蛋。 |
| AC-4 | 超月池 → queued（同月第 2 隻帝王雞排隊） | 先發+核可一隻 emperor（spent→5000）。第二位員工（各有 3 金蛋）`request_redemption(type=emperor, eggs_spent=3)` → status='queued'、queued_for=次月1號、cash_amount=5000；**蛋仍被扣**（refund 不發）。 |
| AC-5 | 帝王後仍可發金蛋小額 | spent=5000（budget=6000）。`request_redemption(type=gold_egg, eggs_spent=1)` cash=500 → 5500<=6000 → status='pending'（不排隊）。 |
| AC-6 | approve 累加 spent | 一筆 pending cash=500、spent=0 → `approve_redemption` → status='approved'、pool.monthly_pool_spent=Decimal("500.0000")、reviewed_by/at 已寫。 |
| AC-7 | approve 防併發超發 | spent=5500（budget=6000），一筆 pending emperor cash=5000 → `approve_redemption` → 拋 `BudgetExceededError`；status **保持** pending、spent **不變**。 |
| AC-8 | mark_paid | approved 一筆 → `mark_paid` → status='paid'、paid_at 非 NULL；ledger **無**新增（蛋早扣）；spent 不變。 |
| AC-9 | reject 退蛋（pending） | pending cash=500、eggs_spent=1 → `reject_redemption` → status='rejected'；新增 ledger gold delta=+1 reason='redeem.refund'；金蛋餘額回升 +1；spent 不變（pending 本未佔池）。 |
| AC-10 | reject 退蛋 + 回沖（approved） | approved cash=500（spent=500）→ `reject_redemption` → status='rejected'、退 1 金蛋、pool.monthly_pool_spent 回到 Decimal("0.0000")。 |
| AC-11 | 月初重置後排隊轉正 | 一筆 queued emperor cash=5000（次月）。`process_queued_for_period(new_period=次月)` → pool.monthly_pool_spent=0、pool_period=次月、該 redemption status='pending'、queued_for=NULL；回傳 list 含這筆。 |
| AC-12 | 排隊 FIFO 兩筆只轉得下一筆 | budget=6000，兩筆 queued emperor（各 5000，requested_at 先 A 後 B）。`process_queued_for_period` → A 轉 pending、B **保持** queued（5000+5000>6000）；回傳 list 只含 A。 |
| AC-13 | 非法狀態轉移拒絕 | 對 status='paid' 的 redemption 呼叫 `approve_redemption` / `reject_redemption` / `mark_paid` 任一 → 拋 `ConflictError`；狀態與帳本不變。 |
| AC-14 | mark_paid 需先 approved | 對 status='pending' 直接 `mark_paid` → 拋 `ConflictError`。 |
| AC-15 | 找不到 pool / redemption | `request_redemption` 對沒有 pool 的 store → `NotFoundError`；對不存在 redemption_id 操作 → `NotFoundError`。 |
| AC-16 | 稽核落地 | 每個成功的 request/approve/paid/reject/dequeue 都新增一筆 `audit_log`（action 為 `reward.*`），且 redemption 全程不產生任何「扣真實薪資」的負向真錢帳。 |

---

## Edge cases

- **跨租戶隔離**：所有讀寫都 `WHERE tenant_id = :tenant_id`；別店的 redemption_id 視為 `NotFoundError`。
- **金額量化**：`cash_amount` 為 `Money`（Numeric(14,4)）；以 `Decimal` 算，寫入維持 4dp；測試斷言用 `Decimal("500.0000")` 形狀。
- **monthly_goal_bonus = 0**：`request_redemption(type=monthly_goal)` 但 pool.monthly_goal_bonus<=0 → `ValidationError`（沒有可發金額不該建單）。
- **eggs_spent <= 0**：拋 `ValidationError`（兌換必扣蛋，gold_egg/emperor 皆 > 0）。
- **spent 回沖夾下限**：reject approved 時 `monthly_pool_spent = max(0, spent - cash_amount)`，永不為負。
- **queued 邊界**：`spent + cash_amount == budget`（剛好填滿）→ 允許 pending（`<=` 非 `<`）。
- **process 重入**：同月對同 store 重複呼叫 `process_queued_for_period(同 period)` 應冪等於「依當前 spent 再掃一次」，不重複轉已 pending 的單、不重置已佔用的 spent（實作上以 `pool.pool_period != new_period` 判斷是否需重置；相同 period 則只續掃 queued）。
- **空 queue**：`process_queued_for_period` 無 queued → 仍正常重置 spent/period、回傳空 list。
- **並發兩筆 pending 都還沒核可**：spent 只在 approve 才動，故兩筆都可停在 pending；真正擋超發在 approve 的重驗（AC-7）。

---

## Constraints（hard requirements）

- 單一 service 檔：`restaurant_api/services/employee_reward_service.py`；單一測試檔：`tests/test_employee_reward_service.py`。
- async 函式，吃 `session: AsyncSession`；**只 `flush()`，永不 `commit()`**（交易邊界在 `api/deps.py`）。
- 金錢一律 `Decimal`（對應 `Money`=Numeric(14,4)）；蛋數量一律 `int`；**任何地方不得出現 `float`**。
- Domain 錯誤一律拋 `restaurant_api/api/errors.py` 的 `DomainError` 系列；**不**用 raw `HTTPException`。新增子類 `InsufficientEggsError`、`BudgetExceededError`（繼承 `ConflictError` 或 `DomainError`，給穩定 `code`）；找不到用既有 `NotFoundError`、輸入不合法用既有 `ValidationError`。
- 每筆 request / approve / paid / reject / dequeue 走 `services/audit_service.audit(session, action="reward.*", tenant_id=..., target=("employee_reward_redemptions", id), actor_id=..., before/after=...)`。
- 蛋餘額一律由 `employee_egg_ledger` 加總（SSOT），**不**信任 `employee_pets` 快取欄位。
- 扣蛋寫 `reason='redeem.cash'`（delta<0）、退蛋寫 `reason='redeem.refund'`（delta>0）；帳本 append-only，修正只寫反向 row，**不** UPDATE/DELETE。
- 真錢**只正向**：任何路徑都不可產生扣員工真實薪資的真錢帳；reject 只退「遊戲內的蛋」，不碰薪資。
- 狀態機嚴格：非法轉移拋 `ConflictError`；每個寫操作前以 `_assert_status` 檢查。
- 型別標註齊全；公開 API 為上述 5 個 async 函式，其餘 `_` 私有。
- 模組層常數命名來源值，不寫 magic number（金額一律讀 pool 設定，非硬編）。

---

## Out of scope（重申）

- 發蛋 / 餵食 / 進化 / 蛋兌換階梯 → `employee_pet_service`。
- 考勤結算發蛋與 cron 排程 → `employee_egg_settlement_job`（本 service 只給可被呼叫的 `process_queued_for_period`）。
- HTTP / FastAPI / router / Pydantic 請求回應 schema → `employee_pets` router。
- ORM model / Alembic migration → `employee_pet_models.md`。
- 多幣別 / 匯率（僅 TWD）。
- pet 快取餘額回寫一致性 → `employee_pet_service`。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `models/gamification.py`（`employee_pet_models.md`） | import `EmployeeRewardRedemption`、`EmployeeRewardPool`、`EmployeeEggLedger`、enum `RedemptionType`/`RedemptionStatus`/`EggType` |
| `services/audit_service.audit()` | 每個寫路徑落稽核（action=`reward.requested/queued/approved/paid/rejected/dequeued`） |
| `api/errors.py` | `DomainError`/`NotFoundError`/`ConflictError`/`ValidationError` + 新增 `InsufficientEggsError`/`BudgetExceededError` |
| `api/deps.py::get_db` | 交易邊界（commit-on-success）；本 service 只 flush |
| `employee_pet_service`（鄰 spec） | 對接點：本 service 扣 / 退 gold 蛋，pet service 負責 pet 快取餘額重算；兩者共用 `employee_egg_ledger` SSOT |
| `employee_egg_settlement_job`（鄰 spec） | 月初呼叫 `process_queued_for_period`；每日結算不在此 |
| `employee_pets` router（鄰 spec） | HTTP 入口，把 5 個函式接成 endpoint，注入 tenant_id/actor_id、轉 Pydantic 回應 |
| `tests/conftest.py` | async fixtures、SAVEPOINT、seed_tenant/seed_store |

— end of spec —
