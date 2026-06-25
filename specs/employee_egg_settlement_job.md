# Spec: Employee Egg Daily Settlement Job

> **Module name:** `restaurant_api.jobs.employee_egg_settlement`
> **Owner domain:** HR / Employee Engagement（養成遊戲化）
> **Status:** Spec, ready for orchestrator hand-implementation
> **Implementation target:** 一個新 job 模組 `restaurant_api/jobs/employee_egg_settlement.py` + 排程註冊掛進 `restaurant_api/jobs/__init__.py`
> **依賴契約:** `specs/employee_pet_models.md`(7 表 + 帳本不變式)、`docs/18_employee_pet_gamification.md`(PRD、附錄 B/C、§5.3)
> **服務契約:** `employee_pet_service`、`employee_reward_service`(spec 待寫;本 job **呼叫** service 發蛋/兌換,不自寫帳本邏輯)

---

## Background

養成遊戲化的「水龍頭(faucet)」核心是**考勤自動發蛋**(PRD §5.1)。員工前一日準時上班 → 系統隔天清晨自動發 1 白蛋,無需店長動手(PRD §3 鐵則:90% 的蛋由系統自動發放)。這支背景任務就是那台自動發蛋機:每日台北時間 02:00 結算**前一個 business_date**,把考勤、健康衰退、月池排隊三件事一次掃完。

合規鐵律(PRD D1):曠職記紅蛋、久未餵活力衰退 —— **只影響遊戲內數值,絕不碰真實薪資**。真錢只從 `employee_reward_pool` 正向發放,且本 job 只在月初觸發 service 去處理排隊兌換,不自己算錢。本 job 是頂層 runner(scheduler 直接呼叫),自己管 session 生命週期與 commit,這與「service 不 commit、DI 層 commit」是不同情境 —— 比照既有 `points_expire` / `membership_lifecycle` 的 job 慣例。

---

## Goal

提供一支 async 背景任務 `run_daily_settlement(session, *, for_date) -> SettlementReport`,結算指定 `for_date`(前一營業日)的:(1) 考勤發白蛋(準時)、(2) 曠職紅蛋(只扣遊戲健康)、(3) 久未餵食的 pet 活力衰退,並在 `for_date` 為某月最後一日的隔天(即月初執行時)觸發 `employee_reward_service` 重置月池並處理排隊兌換。整支 job **重跑任一天必須冪等安全**。所有蛋/兌換的帳本寫入一律走 service 層;job 只負責讀資料源(time_clocks / shifts / leave_requests)做判定 + 編排。

---

## Scope

### In scope

- 主函式 `run_daily_settlement(session=None, *, for_date=None)`:`session=None` 時自開 session 並 commit(scheduler 路徑);傳入 session 時由呼叫端管交易(測試路徑)。比照 `points_expire.run_points_expire`。
- **考勤發蛋**:讀 `time_clocks` vs `shifts`,判定每位員工 `for_date` 是否準時上班 → 準時則透過 service 發 1 白蛋(`reason='attendance.ontime'`、帶 `business_date=for_date`)。
- **曠職紅蛋**:有排班(shift)但無對應打卡且當日無核准假 → 透過 service 記 1 紅蛋(`reason='penalty.redegg'`)+ 寫 `decay`/負 health 的 care event(只遊戲內)。
- **遲到/早退**:依 shifts vs time_clocks 判定 → **不發考勤蛋,也不發紅蛋**(反焦慮,呼應 PRD §4.5)。只在 report 計數。
- **活力衰退**:`employee_pets.last_fed_at` 超過 `DECAY_AFTER_DAYS` 未餵 → 透過 service 寫 `decay` care event、降 vitality(只遊戲內)。
- **請假日**:`leave_requests` 中 status=`approved` 且涵蓋 `for_date` 的員工 → 當日不判曠職、不發紅蛋、不斷 streak。
- **月初處理**:當 `for_date` 是某月最後一日(即此次執行時已跨月) → 呼叫 `employee_reward_service.process_queued_for_period(session, period=<新月份1號>)` 重置 `monthly_pool_spent` 並 FIFO 核發排隊 redemptions。
- 排程註冊:掛進 `jobs/__init__.py` 的 `_register`,CronTrigger 02:00 Asia/Taipei,內部把 `for_date` 算成「昨天(台北)」。
- 結構化 `SettlementReport`(每段計數),回傳供測試斷言 + log。

### Out of scope

- 發蛋/餵食/升級/兌換的**業務細節與帳本寫入** → `employee_pet_service` / `employee_reward_service`(本 job 只呼叫)。
- HTTP / FastAPI router / Pydantic 請求回應 schema → `employee_pets` router spec。
- 餘額重算演算法、雞進化規則 → service spec。
- 同儕互送蛋、顧客雙邊 → Phase 2(PRD D8、§6)。
- LINE 推播提醒(餵雞提醒) → 後續(本 job 不送訊息;若要,比照 `membership_lifecycle` 的 post-commit best-effort,但**本 spec 不做**)。
- 多幣別 — 真錢一律 TWD,且本 job 不直接算錢(交給 service)。
- 持久化以外的任何 I/O(無外呼)。

---

## Public interface

```python
async def run_daily_settlement(
    session: AsyncSession | None = None,
    *,
    for_date: date | None = None,
) -> SettlementReport:
    """Settle one business_date (default: yesterday in Asia/Taipei).

    Top-level runner. When ``session is None`` opens its own session and
    commits on success (scheduler path). When a session is supplied the
    caller owns the transaction (tests). Idempotent: re-running any date is
    safe. Egg/redemption writes go through the service layer; this job only
    reads time_clocks / shifts / leave_requests to decide, then orchestrates.
    """
```

```python
class SettlementReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    business_date: date
    ontime_eggs_granted: int       # 新發的考勤白蛋(已扣冪等跳過的)
    ontime_skipped_idempotent: int # 因 partial unique 已存在而跳過
    late_count: int                # 遲到(不發蛋不罰)
    early_leave_count: int         # 早退(不發蛋不罰)
    absent_redeggs: int            # 曠職紅蛋
    on_leave_count: int            # 當日核准請假(豁免)
    decay_applied: int             # 活力衰退的 pet 數
    monthly_pool_reset: bool       # 此次是否觸發月初重置
    queued_redemptions_processed: int  # 月初核發的排隊兌換數
```

> `SettlementReport` 是回報物件(非入庫),`ConfigDict(frozen=True)`,蛋/計數一律 `int`。模組必須以 `run_daily_settlement` 為唯一公開 API;其餘 helper 以 `_` 開頭視為私有。

---

## 結算流程(步驟化 · 含時區換算)

DB 一律存 UTC;準時/曠職判定以 **Asia/Taipei** 的 calendar date 為準。

1. **決定 `for_date`**:呼叫端未傳 → `for_date = (now in Asia/Taipei).date() - 1 day`(昨天台北)。
2. **計算 UTC 視窗**:把 `for_date` 整個台北日(`00:00:00`–`23:59:59.999999 +08:00`)轉成 `[day_start_utc, day_end_utc)` 半開區間,所有 time_clocks / shifts / leave_requests 查詢都用這個 UTC 視窗 filter。
3. **載資料源**(scope 到該視窗):該日所有 `shifts`(scheduled_start 落在視窗內)、所有 `time_clocks`(clock_in 落在視窗內)、所有 `approved` `leave_requests`(`[start_at, end_at]` 與視窗重疊)。
4. **逐 (employee, shift) 判定**(見下方判定表)→ 分類為 ontime / late / early_leave / absent / on_leave。
5. **發考勤蛋**:ontime 者透過 `employee_pet_service.grant_attendance_egg(session, employee_id, business_date=for_date)`;service 內靠 partial unique 冪等,撞 unique 視為「已發」記入 `ontime_skipped_idempotent`、不報錯。
6. **記曠職紅蛋**:absent 且非 on_leave 者透過 `employee_pet_service.apply_penalty_redegg(session, employee_id, business_date=for_date, reason="penalty.redegg")`(只遊戲內 health);自帶冪等(見冪等策略)。
7. **活力衰退**:掃 `employee_pets` 中 `last_fed_at < (for_date - DECAY_AFTER_DAYS)` 或 `last_fed_at IS NULL` 且建立超過 `DECAY_AFTER_DAYS` 的 pet → `employee_pet_service.apply_decay(session, pet_id, business_date=for_date)`;同日同 pet 只衰退一次(冪等)。
8. **月初判定**:若 `for_date` 是其所屬月份的最後一日 → `monthly_pool_reset=True`,呼叫 `employee_reward_service.process_queued_for_period(session, period=date(next_year, next_month, 1))`。
9. **commit**(僅 `session is None` 自開時)、組 `SettlementReport`、`logger.info("employee_egg_settlement.complete", extra=report.model_dump_compatible)`、回傳。

---

## 判定規則表(準時 / 遲到 / 早退 / 曠職 / 請假)

設店家寬限 `GRACE_MINUTES`(模組常數,預設 5)。對每位有當日 `shift` 的員工:

| 情境 | 條件(以該員工該 shift 為準) | 結果 |
|---|---|---|
| **請假(豁免)** | 當日有 `approved` leave_request 與 shift 區間重疊 | 不判曠職、不發紅蛋、**不斷 streak**;`on_leave_count++`;不發考勤蛋 |
| **準時** | 有對應 time_clock 且 `clock_in <= scheduled_start + GRACE_MINUTES` 且 `clock_out >= scheduled_end - GRACE_MINUTES`(或 clock_out 為 NULL 視為仍在班,不算早退) | 發 1 白蛋 `attendance.ontime`;`ontime_eggs_granted++` |
| **遲到** | 有 time_clock 但 `clock_in > scheduled_start + GRACE_MINUTES` | `late_count++`;**不發蛋、不罰紅蛋** |
| **早退** | 有 time_clock 且 `clock_out` 非 NULL 且 `clock_out < scheduled_end - GRACE_MINUTES` | `early_leave_count++`;**不發蛋、不罰紅蛋** |
| **曠職** | 有 shift 但**完全無**對應 time_clock 且當日無核准假 | 記 1 紅蛋 `penalty.redegg` + 降 health(只遊戲內);`absent_redeggs++` |

> 同一員工同日同時遲到又早退 → 計一次 late + 一次 early_leave(都不發蛋);仍不罰紅蛋。配對 shift↔time_clock 以同 `employee_id` + 同台北日為鍵;一員工一日多 shift 的處理:**MVP 以「當日是否有至少一段準時」決定發 1 顆蛋**(每日上限 1 白蛋,PRD §5.1),其餘段只計數。

---

## 冪等策略(硬性)

整支 job 重跑任一天必須安全:

- **考勤白蛋**:靠 models spec 的 partial unique `uq_egg_attendance_once`(`(employee_id, business_date) WHERE reason='attendance.ontime'`)。service 發蛋撞 unique → job 接住、記 `ontime_skipped_idempotent`、不重複發、不報錯。
- **曠職紅蛋**:同日同員工同 `penalty.redegg` 只記一次。service 端在寫前查當日是否已有同 `(employee_id, business_date, reason='penalty.redegg')` 的 ledger row;有則跳過。job 不自查帳本,但 spec 要求 service 提供此冪等保證(本 job 的 AC 會驗「重跑不重複」)。
- **活力衰退**:同日同 pet 只寫一次 `decay` care event。判定鍵:`(pet_id, business_date)`,service 端查當日是否已有 `decay` care event;有則跳過。
- **月初重置**:`process_queued_for_period(period)` 必須對同一 `period` 冪等(重跑不重置已重置過的池、不重複核發已處理的 redemption)—— 由 reward_service 保證,本 job 只負責在「`for_date` 為月底」時呼叫一次。
- **整體**:job 不維護自己的「已執行」標記;冪等完全建立在 ledger 的 partial unique + service 的存在性檢查上(與 `points_expire` 用 reason key + source id lookup 同精神)。

---

## 排程註冊(掛進 jobs/__init__.py)

比照既有 5 支 job 的寫法,在 `_register` 加一段:

```python
scheduler.add_job(
    _wrap("employee_egg_settlement", run_daily_settlement),
    trigger=CronTrigger(hour=2, minute=0, timezone=tz),  # 02:00 Asia/Taipei
    id="employee_egg_settlement",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=600,
)
```

- `_wrap` 期望 `Callable[[], Awaitable[object]]`,而 `run_daily_settlement` 有 kw-only `for_date`、預設 `None` → 可無參數呼叫(自算昨天台北),簽章相容。
- import `run_daily_settlement` 加進 `jobs/__init__.py` 頂部 import 區 + `__all__`。
- 02:00 刻意排在其他 job(03:00 起)之前,避免與 points_expire/cogs 撞;且午夜後台北日已穩定。

---

## Acceptance criteria

> 對應測試 `tests/test_employee_egg_settlement.py`,命名 `test_settlement_ac_NN_*`。用 `tests/conftest.py` 的 async fixtures,每測一個 SAVEPOINT、scope 到 `seed_tenant`/`seed_store`,測試自行 seed 出 shifts + time_clocks + employee_pets;呼叫時**傳入 session**(呼叫端管交易)。金額用 `Decimal`,蛋/計數用 `int`,永不 float。

| # | 名稱 | 驗收條件(worked) |
|---|---|---|
| AC-1 | 準時發 1 白蛋 | 員工 A `for_date` 排班 09:00–18:00、打卡 08:58 進 / 18:03 出 → `ontime_eggs_granted == 1`;ledger 多 1 筆 `attendance.ontime`、`business_date=for_date`、`delta=+1`、`egg_type='white'`。 |
| AC-2 | 重跑冪等不重複發 | 同 AC-1 連跑兩次 → 第二次 `ontime_eggs_granted == 0`、`ontime_skipped_idempotent == 1`;ledger 該員該日 `attendance.ontime` row 數仍為 1(partial unique)。 |
| AC-3 | 遲到不發蛋不罰 | 員工 B 排班 09:00、寬限 5min、打卡 09:30 進 → `late_count == 1`、`ontime_eggs_granted == 0`、`absent_redeggs == 0`;無新 `attendance.ontime`、無 `penalty.redegg`。 |
| AC-4 | 早退不發蛋不罰 | 員工 C 排班 …–18:00、打卡 17:30 出 → `early_leave_count == 1`、不發蛋、不罰紅蛋。 |
| AC-5 | 寬限邊界 | 員工 D 排班 09:00、`GRACE_MINUTES=5`、打卡 09:05:00 進 → 仍算準時(`<=` 邊界)、發 1 蛋;09:05:01 → late。 |
| AC-6 | 曠職記紅蛋且不扣薪 | 員工 E 有 shift、**無**打卡、無假 → `absent_redeggs == 1`;ledger 多 1 筆 `penalty.redegg`;對應 care event health_delta < 0;**斷言:無任何 Money/薪資欄位被改、無 redemption 被建**。 |
| AC-7 | 曠職紅蛋冪等 | AC-6 連跑兩次 → 第二次 `absent_redeggs == 0`;該員該日 `penalty.redegg` row 數仍為 1。 |
| AC-8 | 請假不判曠職、不斷 streak | 員工 F 有 shift、無打卡,但有 `approved` leave_request 涵蓋 for_date → `on_leave_count == 1`、`absent_redeggs == 0`;pet `feeding_streak_days` 不被歸零。 |
| AC-9 | 久未餵活力衰退 | 員工 G 的 pet `last_fed_at = for_date - 4 天`、`DECAY_AFTER_DAYS=3` → `decay_applied == 1`;pet `vitality` 下降、寫 1 筆 `decay` care event;`health` 不因衰退連動真錢(只遊戲內)。 |
| AC-10 | 衰退冪等 | AC-9 連跑兩次 → 第二次 `decay_applied == 0`;該 pet 該日 `decay` care event 仍為 1。 |
| AC-11 | 最近有餵不衰退 | 員工 H 的 pet `last_fed_at = for_date - 1 天` → 不在衰退範圍、`decay_applied` 不含 H。 |
| AC-12 | 月初重置 + 處理排隊 | `for_date = 某月最後一日`,reward_service stub 有 2 筆 `queued` redemption → `monthly_pool_reset == True`、`queued_redemptions_processed == 2`;`process_queued_for_period` 被以 `period=次月1號` 呼叫一次。 |
| AC-13 | 非月底不重置 | `for_date` 為月中某日 → `monthly_pool_reset == False`、`queued_redemptions_processed == 0`、`process_queued_for_period` 未被呼叫。 |
| AC-14 | 時區換算正確 | 種一筆台北日 `for_date` 23:30(= UTC 15:30 同日)的準時打卡 → 被正確歸入 `for_date`、發蛋;種一筆台北次日 00:10(= UTC 16:10)的打卡 → **不**歸入 `for_date`。 |
| AC-15 | 預設昨天 | 不傳 `for_date` 呼叫 → `report.business_date == (今天台北 - 1 日)`;不傳 session 時自開並 commit(可用 `points_expire` 同款 `session=None` 路徑驗,測試以 fixture session 包覆或 mock sessionmaker)。 |

---

## Edge cases

- **某員工當日多段 shift**:只要任一段準時 → 發 1 白蛋(每日上限 1);多段都遲到 → 計多次 late、0 蛋、0 紅蛋。
- **clock_out 為 NULL(忘打下班)**:不視為早退(可能還在班/系統漏記);準時判定只看 clock_in 在寬限內即可發蛋,不因 NULL clock_out 罰。
- **有打卡但無 shift(臨時支援)**:無排班基準 → 不發考勤蛋(MVP 只獎勵「對排班準時」);不報錯、不計入任何懲罰。
- **leave_request 部分重疊**(請假只涵蓋半天而 shift 整天):MVP 採「只要 approved leave 與 shift 區間有重疊即豁免當日」,不做半天細分(後續模組)。
- **員工尚無 pet**:曠職紅蛋仍可寫 ledger(帳本以 employee 為鍵),但 care event/衰退需 pet → 無 pet 則跳過 care event;service 決定,job 容忍。
- **`for_date` 是月底也是月初的隔天**:月初重置以「`for_date` 為月底 → 處理次月」一條路徑判定,避免 02:00 跑時對「今天是 1 號」誤判(用 for_date 而非 now)。
- **空店(該日無任何 shift)**:所有計數 = 0、`monthly_pool_reset` 視 for_date 而定;不報錯。
- **重跑整月**:逐日重跑皆冪等(AC-2/7/10),月初重置對同 period 亦冪等。

---

## Constraints(硬性)

- **單一 job 檔**:`restaurant_api/jobs/employee_egg_settlement.py`(+ 改 `jobs/__init__.py` 註冊,不另開檔)。
- **單一測試檔**:`tests/test_employee_egg_settlement.py`。
- **冪等**:整支 job 重跑任一天必須安全(考勤蛋靠 partial unique;紅蛋/衰退靠 service 存在性檢查;月初靠 period 冪等)。
- **金錢 `Decimal`、蛋/計數 `int`、永不 `float`**。
- **帳本邏輯走 service**:job **不**直接 INSERT `employee_egg_ledger` / `employee_pet_care_events`,一律呼叫 `employee_pet_service` / `employee_reward_service`;job 只可**讀** time_clocks / shifts / leave_requests / employee_pets 做判定。
- **稽核**:發紅蛋/兌換等敏感動作的 audit 由 service 內 `audit_service.audit()` 寫(job 不直接寫 AuditLog)。
- **提交邊界**:`session is None` 時 job 自開 session 並於成功後 `commit()`(頂層 runner 慣例,比照 `points_expire` / `membership_lifecycle`);傳入 session 時不 commit、由呼叫端管。**此處 job commit 是合法的**(不違反「service 不 commit」—— 那條規範針對 DI 層的 service,不針對頂層 job runner)。
- **時區**:DB 存 UTC;判定用 Asia/Taipei(`settings.default_timezone`);for_date → UTC 視窗用半開區間 `[start, end)`。
- **結構化日誌**:`logger = logging.getLogger("restaurant_api.jobs.employee_egg_settlement")`;完成寫 `logger.info("employee_egg_settlement.complete", extra={...})`;**不要**把祕密/薪資塞進 extra。
- **遊戲內限定**:紅蛋/衰退只動 `health`/`vitality`/care event,**絕不**寫任何 Money 欄位或建 redemption(PRD D1)。
- **無 magic numbers**:`GRACE_MINUTES = 5`、`DECAY_AFTER_DAYS = 3` 為模組層級常數(可被 service/設定覆寫的延伸留待後續)。
- **依賴**:專案既有棧(SQLAlchemy async / pydantic≥2.5 / APScheduler);不新增第三方庫。
- **型別標註**:每個公開/私有函式完整 type hints;`from __future__ import annotations`。

---

## Out of scope(重申,避免 Coder drift)

- 發蛋/餵食/升級/兌換的帳本實作與餘額重算 → `employee_pet_service` / `employee_reward_service`。
- HTTP / FastAPI router / 請求回應 schema → `employee_pets` router。
- 同儕互送蛋、顧客雙邊飛輪 → Phase 2(PRD D8、§6)。
- LINE 推播 / 餵雞提醒。
- 半天請假細分、跨日 shift 的精算。
- 雞進化條件判定(升級邏輯)→ service。
- 真錢計算 / 多幣別。

---

## Connection to other modules

| Module | 介面 |
|---|---|
| `restaurant_api/jobs/__init__.py` | `_register` 加 cron 02:00、import + `__all__` 加 `run_daily_settlement`(比照 5 支既有 job) |
| `restaurant_api/jobs/points_expire.py` | **session 生命週期/冪等/commit 範本**(`session=None` 自開 + commit) |
| `restaurant_api/jobs/membership_lifecycle.py` | **多段結算 + 結構化 report 範本** |
| `restaurant_api/models/hr.py` | 判定資料源:`Shift` / `TimeClock` / `LeaveRequest`(+ `LeaveStatus.APPROVED`) |
| `restaurant_api/models/gamification.py`(待 models spec 落地) | 讀 `employee_pets`(`last_fed_at`);寫入經 service |
| `employee_pet_service`(待 spec) | `grant_attendance_egg` / `apply_penalty_redegg` / `apply_decay`(job 呼叫;冪等由 service 保證) |
| `employee_reward_service`(待 spec) | `process_queued_for_period(session, period)`(月初呼叫;period 冪等) |
| `restaurant_api/config.py` | `settings.default_timezone`(Asia/Taipei) |
| `restaurant_api/services/audit_service.audit()` | 由 service 內呼叫(本 job 不直接寫) |

---

## 給 PM Agent 的提醒

- **service 介面尚未定稿**:`employee_pet_service` / `employee_reward_service` spec 還沒寫。本 job 假設的方法名(`grant_attendance_egg` / `apply_penalty_redegg` / `apply_decay` / `process_queued_for_period`)是**契約期望**;若後續 service spec 命名不同,以 service spec 為準並回頭調整本 job,但「job 呼叫 service、不自寫帳本」的分工不變。
- **反焦慮是設計核心**(PRD §4.5):遲到/早退**只計數不懲罰**,曠職才罰紅蛋;請假日不斷 streak。不要為了「更嚴格」加扣分,會反噬留任。
- **D1 紅線**:紅蛋/衰退**只動遊戲內數值**。任何測試或實作只要碰到 Money 欄位或建 redemption 就是 bug。
- **冪等是這支 job 的命脈**:scheduler 可能 misfire 補跑、手動補算整月。考勤蛋靠 DB partial unique 天然冪等;紅蛋/衰退靠 service 存在性檢查 —— AC-2/7/10 必須真的連跑兩次驗 row 數不變。
- **時區陷阱**:用 `for_date`(昨天台北)反推 UTC 半開視窗,**不要**用 `now()` 直接比;月初判定也用 `for_date 是否月底`,不要用「今天是 1 號」(02:00 跑時今天確實是 1 號,但語意應綁 for_date)。
- **每日上限 1 白蛋**(PRD §5.1):一員工一日多段 shift 也只發 1 顆。
- 別讓 job 直接 INSERT ledger:那會繞過 service 的餘額快取維護與 audit,破壞 SSOT 一致性。
- 測試用 conftest 的 async `session` fixture + SAVEPOINT,**傳入 session** 呼叫(不要走自開 session 路徑,會撞 event loop / 真 commit);AC-15 的「自開+commit」可用 mock sessionmaker 或單獨標記。

— end of spec —
