# Task Brief: Employee Pet Service (養成遊戲化核心 service)

> **Module name:** `restaurant_api.services.employee_pet_service`
> **Owner domain:** HR / Employee Engagement（養成遊戲化）
> **Status:** Spec, ready for PM → Architect → Coder → QA
> **Implementation target:** 一個新 service 模組 `restaurant_api/services/employee_pet_service.py` + 一份測試 `tests/test_employee_pet_service.py`
> **Data-layer contract:** `specs/employee_pet_models.md`（**所有表名/欄位名/enum 值域以該 spec 為準，不得更動**）
> **PRD source of truth:** `docs/18_employee_pet_gamification.md`（附錄 A 蛋經濟/進化/餵食/健康；§5 經濟系統 faucet/sink）

---

## Background

員工養成遊戲化系統把考勤／任務／學習換皮成「養一隻電子雞」。員工領蛋（white/silver/gold）→
餵雞 → 雞升級進化（小雞→母雞→大雞→帝王雞）→ 達里程碑換真實獎金（正向加碼、需審核、月池上限）。
資料層（7 表）已由 `specs/employee_pet_models.md` 定義；蛋的 SSOT 是 append-only 帳本
`employee_egg_ledger`，`employee_pets` 上的餘額是**快取**、可由帳本重算。

本 spec 定義**核心 service 層**：把「發蛋 / 兌換 / 餵食 / 進化 / 對帳」這些業務動作寫成
async service 函式。它**不是**純函式（吃 `session: AsyncSession`、讀寫多張表），但**遵守專案
service 慣例**：只 `flush()`、**不** `commit()`（commit 在 DI 層）；例外用 `DomainError` 系列；
寫稽核走 `audit_service.audit()`。

**核心合規（PRD D1，務必嚴守）**：紅蛋／生病／活力衰退**只影響遊戲內**數值，**絕不**連結真實
薪資。真錢只從 `employee_reward_pool` 正向發放，且**真錢兌換不在本 service**（在
`employee_reward_service`）——本 service 只到「標記帝王雞資格」為止。

---

## Goal

提供一組 async service 函式，封裝員工養成遊戲的軟貨幣（蛋）與寵物狀態變更：發蛋、升階兌換、
餵食（蛋/飼料）、進化判定、餘額重算、streak 計算。每個寫入動作都**同時**寫帳本（SSOT）+
更新 `employee_pets` 快取餘額，維持兩者一致；門檻與兌換率寫成模組層級常數，餘額不足或狀態
非法時拋 `DomainError`。

---

## Scope

### In scope

- `grant_eggs(...)`：寫一筆 `employee_egg_ledger`（signed `delta > 0`）+ 同步更新 pet 快取餘額。
- `exchange_eggs(...)`：白→銀、銀→金 升階兌換，依 pool 率寫**兩筆**帳本（`exchange.down` + `exchange.up`），更新快取；餘額不足拋 `DomainError`。
- `feed_pet(...)`：餵蛋（推升級/進化）或餵飼料（維持健康），寫 `employee_pet_care_events`、扣資源、更新 health/vitality/feeding_streak_days/last_fed_at/level；之後呼叫進化判定。
- `_maybe_evolve(...)`：依 PRD 附錄 A.2 門檻判定階段晉升；門檻為模組層級常數（無 magic number）。
- `recompute_balances(...)`：從 `employee_egg_ledger` 加總重算 pet 的白/銀/金/紅快取餘額（對帳/修復用）。
- `apply_decay(...)`：久未餵食的活力衰退 —— 降 vitality、寫 `decay` care_event（**只遊戲內**，不碰真錢/薪資，PRD D1）。由 job 每日呼叫。
- streak 計算：連續餵養天數；請假日不斷 streak（反焦慮，PRD A.3/4.5）。
- **任務流與讀取 API（router 委派）**：`create_pet`、`get_pet_dashboard`、`store_leaderboard`、`list_today_tasks`、`submit_task_completion`、`review_completion`（核可時內部呼叫 `grant_eggs`，靠 `egg_granted` 旗標冪等）、`list_pending_completions`。
- 每個變更動作寫 `audit_service.audit()`（dotted action namespace）。

### Out of scope（延後到其他模組，明確排除避免 drift）

- **真錢 redemption**（gold_egg/emperor/monthly_goal 兌現、月池扣抵、排隊）→ `employee_reward_service` spec。本 service 帝王雞達標**只標記資格**（回傳 flag / 寫稽核），不寫 `employee_reward_redemptions`、不碰 `employee_reward_pool` 金額。
- **考勤每日結算的排程與判定**（準時/遲到/曠職判定、cron）→ `employee_egg_settlement_job` spec。本 service 提供 job 呼叫的 `grant_eggs`（attendance.ontime 白蛋 / penalty.redegg 紅蛋）與 `apply_decay`，**不自含排程/批次掃描**。
- **HTTP / FastAPI router / Pydantic 請求回應 schema** → `employee_pets` router spec。本 service 提供 router 委派的 `create_pet` / `get_pet_dashboard` / `store_leaderboard` / 任務流函式。
- **真錢 redemption 工作流**（兌現/月池/排隊）→ `employee_reward_service` spec（本 service 帝王雞只標資格旗標）。
- **任務「定義」(employee_tasks) 的後台 CRUD**（建立/編輯任務模板）→ Phase B 管理後台；本 service 只做「完成提交 + 核可發蛋 + 今日清單查詢」。
- **顧客雙邊**（顧客餵店員、養店雞）→ Phase 2。
- **同儕互送蛋 / 紅蛋 debuff 互動** → Phase 2（D8）。
- **多幣別**：金額一律 TWD（`Decimal`）。
- **持久化交易邊界**：不 `commit()`、不開新 session、不管連線生命週期。

---

## Public interface（每個 async 函式的簽章與語意）

> 下列為**錨定簽章**。Architect 可微調參數命名/順序，但語意、回傳語意、跨表不變式不得更動。
> 所有函式吃 `session: AsyncSession` 為第一參數；蛋數量一律 `int`；金額（若有）一律 `Decimal`。

```python
async def grant_eggs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    egg_type: str,            # EggType 值域：white/silver/gold/red
    qty: int,                 # > 0；發放顆數
    reason: str,              # dotted: task.complete / learning.upload / streak.bonus / manual.adjust / penalty.redegg ...
    business_date: date | None = None,
    source_ref: str | None = None,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> EmployeePet:
    """發蛋：寫一筆 ledger(delta=+qty) + 更新 pet 對應快取餘額，回傳更新後的 pet。"""


async def exchange_eggs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    from_type: str,           # "white" 或 "silver"
    to_type: str,             # "silver" 或 "gold"（必須是 from 的相鄰高階）
    to_qty: int,              # > 0；要兌出幾顆高階蛋
    actor_id: uuid.UUID | None = None,
) -> EmployeePet:
    """升階兌換：依 pool 率算出需扣低階數，寫兩筆 ledger
    (exchange.down delta=-low_needed, exchange.up delta=+to_qty)，更新兩種快取餘額。
    低階餘額不足 → InsufficientEggsError。"""


async def feed_pet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    mode: str,                # "egg"（餵蛋推升級/進化）或 "food"（餵飼料維持健康）
    egg_type: str | None = None,   # mode=="egg" 必填；mode=="food" 須為 None
    amount: int = 1,          # > 0；餵幾顆/幾份
    on_date: date | None = None,   # 餵食歸屬日（用於 streak），預設今天(台北)
    actor_id: uuid.UUID | None = None,
) -> EmployeePet:
    """餵食：扣對應資源（蛋餘額或 feed_balance）、寫 care_event、更新
    health/vitality/feeding_streak_days/last_fed_at（+ level if 餵蛋），再呼叫
    _maybe_evolve。資源不足 → InsufficientEggsError。"""


async def recompute_balances(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
) -> EmployeePet:
    """從 employee_egg_ledger 加總 (group by egg_type) 重算白/銀/金快取餘額與 red_count，
    寫回 pet。對帳/修復用；不寫 ledger（不改 SSOT）。回傳修正後的 pet。"""


async def apply_decay(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    on_date: date | None = None,
) -> EmployeePet:
    """活力衰退：若 last_fed_at 距 on_date 超過門檻天數，降 vitality（clamp 0..100）、
    寫一筆 care_event(event_type='decay', vitality_delta<0)。只影響遊戲內（PRD D1）。
    同員工同日冪等（重跑不重複衰退）。回傳更新後的 pet。"""


async def _maybe_evolve(pet: EmployeePet, *, all_attendance_this_month: bool) -> bool:
    """依進化常數判定 pet.stage 是否晉升；就地更新 pet.stage（必要時 nest_level）。
    帝王雞達標僅設資格旗標（emperor_eligible），真錢兌換在 reward service。
    回傳是否發生晉升。模組私有（_ 前綴）。"""
```

### 任務流與讀取 API（router 委派；reads 比照 orders_router 的 GET 慣例）

```python
async def create_pet(
    session: AsyncSession, *, tenant_id, employee_id, store_id, name: str,
) -> EmployeePet:
    """首次建立並命名雞（一員工一雞）。已存在 → ConflictError。"""

async def get_pet_dashboard(
    session: AsyncSession, *, tenant_id, employee_id,
) -> EmployeePet:
    """主畫面資料（雞狀態 + 蛋包餘額 + streak + 階段）。不存在 → NotFoundError。"""

async def store_leaderboard(
    session: AsyncSession, *, tenant_id, store_id, limit: int = 20,
) -> list[EmployeePet]:
    """本店排行榜（依 level/stage 排序，scope 到 store）。"""

async def list_today_tasks(
    session: AsyncSession, *, tenant_id, employee_id, on_date: date | None = None,
) -> list[dict]:
    """今日有效任務 + 該員工當日完成狀態（submitted/approved/rejected/未做）。"""

async def submit_task_completion(
    session: AsyncSession, *, tenant_id, employee_id, task_id, evidence_url: str | None = None,
    on_date: date | None = None,
) -> EmployeeTaskCompletion:
    """提交任務完成。需佐證卻沒給 → ValidationError。同員工同任務同日已 submitted/approved
    → ConflictError（靠 uq_task_completion_daily）。不需審核的任務：直接 approved 並呼叫 grant_eggs。"""

async def review_completion(
    session: AsyncSession, *, tenant_id, completion_id, reviewer_id, approve: bool,
) -> EmployeeTaskCompletion:
    """核可/退回任務完成。approve=True → status=approved + 呼叫 grant_eggs(task.egg_type,
    task.egg_qty, reason='task.complete')，靠 egg_granted 旗標防重複發；approve=False → rejected。
    非 submitted 狀態再審 → ConflictError。"""

async def list_pending_completions(
    session: AsyncSession, *, tenant_id, store_id,
) -> list[EmployeeTaskCompletion]:
    """店長待審：status='submitted' 的任務完成（scope 到 store）。"""
```

> 公開 API = 上述全部 async 函式 + 例外類別。其他 helper（streak 計算、等值換算）以 `_` 開頭視為私有。

---

## 進化與經濟常數（模組層級，無 magic number）

依 PRD 附錄 A.2，全部寫成模組層級常數（不可硬寫在邏輯中）：

```python
# 兌換率預設（實際以 employee_reward_pool 設定為準；常數僅作 fallback 與測試錨點）
DEFAULT_WHITE_PER_SILVER = 10      # 10 白 → 1 銀
DEFAULT_SILVER_PER_GOLD = 10       # 10 銀 → 1 金

# 等值換算（用於進化門檻的「累積 N 蛋等值」判定）
WHITE_PER_SILVER = 10
SILVER_PER_GOLD = 10
# → 1 金蛋等值 = 100 白蛋等值

# 進化門檻（附錄 A.2）
HEN_WHITE_EQUIV = 10               # 母雞：累積 10 白蛋等值
HEN_STREAK_DAYS = 7                # 母雞：連續餵養 ≥ 7 天 → 解鎖雞窩(nest_level→1)
BIG_GOLD_EQUIV = 1                 # 大雞：累積 1 金蛋等值（=100 白等值）
BIG_MIN_HEALTH = 70               # 大雞：health ≥ 70
EMPEROR_GOLD_EQUIV = 3             # 帝王雞：累積 3 金蛋等值（=300 白等值）+ 當月全勤

# streak / 餵食效果
STREAK_FOOD_BONUS_EVERY = 3        # 連續 3 天餵養得淨化飼料（PRD A.3）
FOOD_HEALTH_DELTA = 10             # 餵飼料回復健康
EGG_VITALITY_DELTA = 5             # 餵蛋提升活力
HEALTH_MIN, HEALTH_MAX = 0, 100    # clamp 邊界（呼應 CHECK 0..100）
VITALITY_MIN, VITALITY_MAX = 0, 100
```

> 「累積 N 蛋等值」= 以白蛋為單位的歷史累積（用 ledger 正向 delta 加總換算，或快取餘額換算 —
> Architect 擇一並寫明；測試以「快取餘額換算」為錨點，見 AC）。`1 銀 = 10 白`、`1 金 = 100 白`。

---

## 例外（用 api/errors.py 的 DomainError 系列，不要 raw HTTPException）

模組內定義繼承自 `restaurant_api.api.errors.DomainError` 的子類（或直接 raise 既有的
`ConflictError` / `ValidationError` / `NotFoundError`）：

- `PetNotFoundError(NotFoundError)`：找不到該員工的 pet。
- `InsufficientEggsError(ConflictError)`：兌換/餵食時低階蛋或飼料餘額不足。
- `InvalidExchangeError(ValidationError)`：`from_type`/`to_type` 非相鄰合法升階（如 white→gold、gold→silver）。
- `InvalidFeedError(ValidationError)`：`mode`/`egg_type` 組合非法（如 mode=="egg" 但 egg_type=None）。

`details` 帶可診斷欄位（如 `{"have": 7, "need": 10, "egg_type": "white"}`）。

---

## Acceptance Criteria

> 對應測試 `tests/test_employee_pet_service.py`，命名 `test_svc_ac_NN_*`。用 `tests/conftest.py`
> 的 async fixtures（`AsyncClient`/session）、每測一個 **SAVEPOINT**、跑完 rollback、
> 查詢一律 scope 到 `seed_tenant` / `seed_store`，**不要**全表掃描。蛋數量用 `int`、金額用 `Decimal`。

| # | 名稱 | 驗收條件（worked numbers） |
|---|---|---|
| AC-1 | grant 寫帳本+更新快取 | pet 白=0；`grant_eggs(egg_type="white", qty=3, reason="task.complete")` → 新增 1 筆 ledger（delta=+3, reason="task.complete"）且 `pet.white_balance == 3`。 |
| AC-2 | grant 多次累加 | 連續 `grant_eggs(white, 3)` 兩次 → ledger 2 筆、`pet.white_balance == 6`；帳本加總 == 快取。 |
| AC-3 | grant 紅蛋只進遊戲 | `grant_eggs(egg_type="red", qty=1, reason="penalty.redegg")` → `pet.red_count == 1`；**斷言不寫任何 reward/薪資相關表**（red 不影響真錢，PRD D1）。 |
| AC-4 | 兌換 白→銀 成功 | pool `white_per_silver=10`；pet 白=25 → `exchange_eggs(white, silver, to_qty=2)` → 寫 2 筆 ledger（exchange.down delta=-20、exchange.up delta=+2），`pet.white_balance == 5`、`pet.silver_balance == 2`。 |
| AC-5 | 兌換 銀→金 成功 | pool `silver_per_gold=10`；pet 銀=10 → `exchange_eggs(silver, gold, to_qty=1)` → 銀 -10 / 金 +1，`pet.silver_balance == 0`、`pet.gold_balance == 1`。 |
| AC-6 | 兌換餘額不足拋例外 | pet 白=5 → `exchange_eggs(white, silver, to_qty=1)`（需 10）→ raise `InsufficientEggsError`，details 含 `have=5, need=10`；**不寫任何 ledger**、快取不變。 |
| AC-7 | 非法升階拋例外 | `exchange_eggs(white, gold, ...)` 或 `(gold, silver, ...)` → raise `InvalidExchangeError`；不寫 ledger。 |
| AC-8 | 餵飼料維持健康 | pet health=60、feed_balance=2 → `feed_pet(mode="food", amount=1)` → 寫 1 筆 care_event(event_type="feed_food", health_delta=+10)、`pet.health == 70`、`pet.feed_balance == 1`。 |
| AC-9 | 餵蛋扣蛋+推進度 | pet 白=5、level=1 → `feed_pet(mode="egg", egg_type="white", amount=1)` → ledger delta=-1（餵食消耗）、`pet.white_balance == 4`、care_event(event_type="feed_egg", egg_type="white")、`pet.level` 增加（依規則 ≥ 原值）。 |
| AC-10 | 餵食資源不足拋例外 | pet 白=0 → `feed_pet(mode="egg", egg_type="white")` → raise `InsufficientEggsError`；快取不變、無 care_event。 |
| AC-11 | 進化：母雞門檻 | pet 為 chick、white_balance ≥ 10 白等值且 `feeding_streak_days >= 7` → 餵食後 `_maybe_evolve` 將 `stage == "hen"` 且 `nest_level == 1`（解鎖雞窩）。 |
| AC-12 | 進化：大雞門檻 | pet 為 hen、累積 ≥ 1 金等值（=100 白等值，例 gold_balance=1）且 `health >= 70` → `stage == "big"`。 |
| AC-13 | 進化：帝王雞只標資格不發錢 | pet 為 big、累積 ≥ 3 金等值（gold_balance=3）且當月全勤(`all_attendance_this_month=True`）→ `stage == "emperor"` 且回傳/標記 `emperor_eligible=True`；**斷言不建立 `employee_reward_redemptions`、不改 `employee_reward_pool`**（真錢在 reward service）。 |
| AC-14 | streak：連續天 +1 | `last_fed_at` 為昨日、streak=3 → 今日 `feed_pet` → `feeding_streak_days == 4`、`last_fed_at` 更新為今日。 |
| AC-15 | streak：請假日不斷 | streak=5、上次餵食在 2 天前但中間那天是該員工請假日 → 今日 `feed_pet` → streak 視為連續（`== 6`），不歸零（PRD 反焦慮 A.3/4.5）。 |
| AC-16 | recompute 修復快取 | 人為把 `pet.white_balance` 改成錯誤值（如 999）後呼叫 `recompute_balances` → 重算回帳本加總值（如 6）；ledger 一筆都不新增（SSOT 不變）。 |
| AC-17 | 每動作寫稽核 | `grant_eggs` / `exchange_eggs` / `feed_pet` 各呼叫一次 `audit_service.audit()`（dotted action，如 `egg.granted` / `egg.exchanged` / `pet.fed`）；可 mock/spy 驗證被呼叫。 |
| AC-18 | 不 commit | 任一 service 函式內**不得**呼叫 `session.commit()`（用 spy/monkeypatch 斷言；commit 在 DI 層）。 |

> AC 數 = 18（≥ 12 達標）。每條附 worked numbers，可直接寫成 pytest。

---

## Edge cases（測試須列舉）

- `grant_eggs(qty=0)` 或負數 → raise `ValidationError`（發放必須 > 0；扣蛋走兌換/餵食的反向 row，不走 grant）。
- 找不到 pet（員工尚未建雞）→ `PetNotFoundError`（本 service 不自動建雛雞；建雞在 router/onboarding）。
- 兌換 `to_qty <= 0` → `InvalidExchangeError`。
- 餵食 `mode=="egg"` 但 `egg_type` 為 None、或 `mode=="food"` 卻給了 `egg_type` → `InvalidFeedError`。
- health/vitality 變更須 clamp 在 0..100（呼應 DB CHECK），餵飼料不會超過 100、衰退不會低於 0。
- streak：`last_fed_at` 為今日已餵過再餵 → streak **不重複 +1**（同日多餵不灌水）。
- 帝王雞門檻達標但已是 emperor → 不重複晉升、不重複標資格（冪等）。
- recompute 時帳本含 reversal（負 delta）→ 加總須含 signed delta，得到正確淨額。
- 紅蛋（red）不參與兌換、不參與進化等值；`exchange_eggs`/`feed_pet` 不接受 `egg_type="red"`。

---

## Constraints（hard requirements，來自 CLAUDE.md）

- **單一 module 檔**：`restaurant_api/services/employee_pet_service.py`。
- **單一測試檔**：`tests/test_employee_pet_service.py`。
- **金額用 `Decimal`、蛋數量用 `int`，永不 `float`**。
- **async service**：每函式吃 `session: AsyncSession`；**不可** `session.commit()`（commit 在 DI 層）。只 `await session.flush()`。
- **不開新 session、不查連線、無 HTTP、無全域可變狀態**。
- **例外用 `api/errors.py` 的 `DomainError` 系列**，不要 raw `HTTPException`、不要 raw `ValueError` 外洩。
- **寫稽核一律走 `audit_service.audit(...)`**（dotted action namespace），不要直接 INSERT `AuditLog`。
- **餘額快取與帳本 SSOT 必一致**：任何發/扣蛋同時寫帳本 + 更新快取；reversal 用反向 row，不 UPDATE/DELETE ledger。
- **表名/欄位名/enum 值域**完全沿用 `specs/employee_pet_models.md`（white/silver/gold/red、chick/hen/big/emperor、reason dotted namespace 等）。
- **無 magic number**：進化門檻、兌換率、health/vitality delta 全為模組層級常數。
- **型別標註**：每個公開/私有函式完整 type hints。
- **PRD D1 合規**：red_count/health/vitality 變更**絕不**觸及 `employee_reward_*` 真錢欄位或任何薪資路徑。
- 依賴僅限專案既有棧（SQLAlchemy async 2.x、Pydantic v2、stdlib）；不引入新第三方。

---

## Out of scope（重申，避免 Coder drift）

- **真錢 redemption / 月池扣抵 / 排隊**（D7）→ `employee_reward_service`。
- **每日結算 job**（準時發蛋、曠職紅蛋、活力衰退掃描）→ `employee_egg_settlement_job`。
- **HTTP / FastAPI router / Pydantic 請求回應 schema** → `employee_pets` router。
- **顧客雙邊 / 同儕互送蛋** → Phase 2。
- **任務「定義」模板的後台 CRUD（建立/編輯 employee_tasks）** → Phase B 管理後台。
  （注意：任務「完成提交 / 核可發蛋 / 今日清單」**在本 service**，見上方任務流 API；建立並命名雞 `create_pet` 也**在本 service**。）
- **多幣別** → TWD only。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `restaurant_api/models/gamification.py` | 讀寫 `employee_pets` / `employee_egg_ledger` / `employee_pet_care_events` / `employee_reward_pool`（只讀兌換率）。表名欄位以 `specs/employee_pet_models.md` 為準。 |
| `restaurant_api/services/audit_service.py::audit` | 每個變更動作寫稽核（`egg.granted` / `egg.exchanged` / `pet.fed` / `pet.evolved`）。 |
| `restaurant_api/api/errors.py` | `DomainError` 系列（本 service 子類 + `Conflict/Validation/NotFound`）。 |
| `restaurant_api/api/deps.py::get_db` | 提供 session 並負責 commit-on-success；本 service 只 flush。 |
| `models/customers.py::CustomerPointsLedger` | 蛋帳本的設計範本（append-only + signed delta + reversal）。 |
| `employee_reward_service`（下一支 spec） | 接手真錢兌換；本 service 標記帝王雞資格後交棒。 |
| `employee_egg_settlement_job`（job spec） | 每日呼叫 `grant_eggs`（attendance.ontime 等）；冪等由 ledger partial unique 保證。 |
| `employee_pets` router（router spec） | HTTP 層呼叫本 service 的四個公開函式。 |
| `models/hr.py`（leave_requests/shifts） | streak 計算讀請假日（請假不斷 streak）。 |

---

## 給 PM Agent 的提醒

- **快取 vs SSOT 的一致性是這支 service 的命脈**：任何發蛋/扣蛋都必須「寫帳本 + 更新快取」在同一個交易內完成；`recompute_balances` 是出問題時的修復閘，不是常態路徑。對帳測試（AC-16）要把它當回歸保護。
- **不要把真錢邏輯滲進來**（PRD D1/D7）：帝王雞達標**只標資格**，真錢兌換、月池 6,000 上限、超額排隊次月，全在 `employee_reward_service`。若 Coder 開始寫 `employee_reward_redemptions` 或改 `monthly_pool_spent`，就是越界。
- **red 蛋是 debuff、不是貨幣**：不可兌換、不可餵食、不參與進化等值；它只增 `red_count` + 扣遊戲健康，永不碰薪資。
- **streak 反焦慮是產品價值不是 bug**：請假日不斷 streak（AC-15）、同日多餵不灌水（edge case）。讀 `leave_requests` 判定請假日，別自己發明假日曆。
- **冪等歸 DB**：考勤發蛋的「同日只發一次」靠 ledger partial unique index（已在 models spec），service 端遇到衝突要轉成乾淨的 `ConflictError`，不要吞掉或自己做去重。
- **進化門檻全可調**：PRD 說 pilot 期週週校準，所以門檻寫成常數（未來可挪到設定表）；測試用常數當錨點即可，不要硬寫數字在斷言外的邏輯裡。
- **commit 紀律**：service 內絕不 commit（AC-18 守門）；測試用 `tests/conftest.py` 的 SAVEPOINT fixture，scope 到 `seed_tenant`/`seed_store`，跑完 rollback。

— end of brief —
