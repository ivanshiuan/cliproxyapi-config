# 18 — 送券裂變 + 貴人配額券 設計文件

> 需求來源:Ivan 2026-07。「會員可以把餐券/優待券送給朋友,朋友領了、來吃了,
> 送券的會員就拿到回饋(點數/抵用),點數累積可換商品」+「給股東/貴人一批
> 額度券,讓他們自己發放請客」。
>
> 這份文件把兩個模式的資料模型、獎勵時機、防刷設計、與現有系統的接點寫清楚。
> **狀態:設計定稿,待實作。** 4 個營運參數已填入建議預設值(見 §5),Ivan 可調。

---

## 1. 一句話

- **模式 A(會員裂變送券)**:衝散客量。會員送券 → 朋友到店核銷 → 會員賺點數。
- **模式 B(貴人配額券)**:做人情、動用資源。發一批有天花板的額度券給股東,
  股東自己發放,你看得到每個人幫你請了多少客。

兩者共用同一套「券 + 錢包 + 核銷」底層,只差在**獎勵規則**和**額度來源**。

---

## 2. 最重要的原則:獎勵綁「核銷」,不綁「分享」

> 🔴 這是整個設計的地基,做錯會直接賠錢。

「每發一張券就給 $10」= 送分給詐欺。有人開 10 個假 LINE 帳號互送券,
一毛新客沒進來,你純賠回饋。

**正確**:回饋只在「**被送的朋友真的到店、櫃台核銷那張券**」的當下才觸發。
- 送券成本只在「真的有人來吃」時發生 → 永遠不會賠。
- 對會員的話術更好:「你送的券有人來吃,你就賺 $10」——他會主動催朋友來。

這與現有 `referral_service.qualify_referral()` 的邏輯一致:推薦獎勵綁在
「被推薦人首次消費」而非「註冊」。我們沿用同一個 one-shot、狀態機守衛的模式。

---

## 3. 資料模型(接在現有結構上,不重蓋)

### 3.1 擴充現有 `campaign_vouchers`

現在券只有一種來源(抽獎中獎)。加一個券種與來源欄位:

```
+ voucher_kind    enum(wheel_prize, cash_credit, item_redeem, hotpot_base, ...)
+ origin          enum(wheel, member_gift, vip_grant)   -- 這張券怎麼來的
+ gift_id         FK voucher_gifts(id) NULL             -- 若來自送券
+ grant_id        FK voucher_grants(id) NULL            -- 若來自貴人額度包
```

券的生命週期 `active → redeemed / expired / void` 完全復用,櫃台核銷流程不變。

### 3.2 新增 `voucher_gifts`(模式 A:一次送券動作)

```
voucher_gifts
  id             PK uuid7
  tenant_id      FK
  sender_id      FK customers(id)      -- 送券的會員
  recipient_id   FK customers(id) NULL -- 領券後綁定的朋友(領前為 NULL)
  voucher_id     FK campaign_vouchers(id) NULL -- 領取後生成的券
  share_token    str(unique)           -- 分享連結裡的 token
  status         enum(pending, claimed, redeemed, expired, revoked)
  reward_kind    enum(points, cash_credit)  -- 回饋型式(見 §5-1)
  reward_amount  Money                 -- 回饋金額/點數
  rewarded_at    timestamptz NULL      -- 回饋發放時間(核銷才填)
  created_at / updated_at
```

狀態流:`pending`(已產生連結)→ `claimed`(朋友加好友領走)→
`redeemed`(朋友到店核銷,**此時發回饋給 sender**)。

### 3.3 新增 `voucher_grants`(模式 B:貴人額度包)

```
voucher_grants
  id             PK uuid7
  tenant_id      FK
  grantee_name   str      -- 股東/貴人名字(不一定是系統會員)
  grantee_id     FK customers(id) NULL -- 若對方也是會員
  voucher_kind   enum     -- 這個額度包發哪種券
  face_value     Money    -- 每張面額(如 $200 抵用)或 0(如肉盤兌換)
  menu_item_id   FK menu_items(id) NULL -- 品項券才有(鍋底/肉盤/菜盤)
  total_quota    int      -- 配額上限(如 20 張)
  used_count     int      -- 已被領走/核銷的數量
  valid_until    date     -- 整包到期日
  created_at / updated_at
```

股東拿到「我的發券頁」連結 → 每發一張從 `total_quota` 扣 → `used_count` 到頂就發完。
**模式 B 不發回饋點數**(股東是用你給的額度做人情,不是賺錢),額度天花板保證不失控。

### 3.4 回饋點數走現有帳本

送券回饋一律寫 `customer_points_ledger`(append-only,DB-level 已擋改),
`reason="gift_referral_reward"`。換商品同樣走現有點數扣抵。**不新增點數表。**

---

## 4. 兩條流程

### 模式 A
```
會員錢包點券 →「送給朋友」→ 產生 share_token 連結(voucher_gifts: pending)
  → 朋友點連結 → 加官方帳號好友 → 券落進朋友錢包(claimed, recipient 綁定)
  → 朋友到店 → 櫃台核銷 → sender +$10 點數(redeemed, 寫 ledger)
  → 點數累積到門檻 → 換商品
```

### 模式 B
```
你後台建 voucher_grants(股東A: $200券×20 + 肉盤×5 …)
  → 股東拿「我的發券頁」連結 → 發給朋友(扣 quota)
  → 朋友領券 → 到店核銷 → grant.used_count +1
  → 你在後台看:每位股東發了幾張/核了幾張/剩多少額度/幫你請了多少錢
```

### 防刷三道鎖(模式 A)
1. 回饋只在朋友**核銷**時發(§2)。
2. 券與**朋友本人 LINE ID** 綁定,同一 ID 不能自送自收。
3. 每會員每月裂變回饋設上限(預設 $500,見 §5-4)。

---

## 5. 4 個營運參數(已填建議預設,Ivan 可調)

| # | 參數 | 建議預設 | 理由 |
|---|---|---|---|
| 1 | 裂變回饋型式 | **點數**(非現金抵用) | 點數成本可控、綁在店內回流;現金抵用對毛利壓力大 |
| 2 | 每次成功回饋 | **100 點(= $10 價值感)** | 對齊需求「$10」;用點數計價保留調整空間 |
| 3 | 換商品門檻範例 | **300 點 → 一份菜盤 / 500 點 → 一份肉盤** | 讓會員 3~5 次成功裂變就能換一次,回饋看得到 |
| 4 | 每會員每月回饋上限 | **$500 價值(= 5000 點)** | 擋極端農場,正常熱心會員碰不到 |
| 5 | 領券是否強制加好友 | **是(強制)** | 每張券都幫你長一個會員,這才是裂變真正的價值 |

> 這些預設之後應搬進 `tenant_settings` 做每品牌可調(目前 `referral_service`
> 的獎勵是模組常數,同型技術債,一起處理)。

---

## 6. 實作切片(待 Ivan 確認預設後開工)

1. **migration**:擴充 `campaign_vouchers` + 建 `voucher_gifts` / `voucher_grants`。
2. **`voucher_gift_service`**:產生分享連結、領取綁定、核銷觸發回饋(one-shot 守衛)。
3. **`voucher_grant_service`**:建額度包、發券扣額、後台額度儀表。
4. **router**:會員送券 API、朋友領券 API(接 LINE 加好友)、貴人發券頁、後台額度查詢。
5. **核銷 hook**:在現有櫃台核銷流程掛回饋觸發(模式 A)+ 扣額(模式 B)。
6. **測試**:含防刷案例(自送自收擋掉、回饋只發一次、月上限生效、額度扣到零)。

全部依 CLAUDE.md 不變法則(Decimal 金錢、tenant 隔離、append-only ledger、
service 不 commit、真 PG 整合測),交付前跑完整使用者路徑複盤。

---

## 7. 待 Ivan 拍板

- §5 的 5 個預設是否 OK?哪個要改直接說數字。
- 模式 B 第一批股東名單 + 每人額度組合(給我一個範本我先建,你複製調整)。
- 這份設計 OK 就開工,不 OK 告訴我哪裡要改。
