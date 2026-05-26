# 08 — 食安 / 勞檢 / 個資 / 災難 治理手冊

> 開店前**必讀**。Phase 1 軟體已準備好支援的合規與危機處理流程。
> 這份不是「未來會做」清單，是「今天就要照這個 SOP 跑」的營運鐵律。

---

## 一、食品安全（食品安全衛生管理法）

### 1.1 食材批號追溯（已落地）

每一筆進貨在 `purchase_order_lines` 帶 `lot_no`（供應商批號）。
寫到 `stock_movements` 時 `lot_no` 也帶上。  
**永遠可以從顧客腹瀉申訴反查到哪批食材害的：**

```sql
-- "5/26 晚上 7-9 點哪幾筆訂單用了 PO-0512 批的雞肉？"
SELECT DISTINCT o.id, o.order_no, o.opened_at
FROM orders o
JOIN order_lines ol ON ol.order_id = o.id
JOIN stock_movements sm
  ON sm.source_table = 'order_lines'
 AND sm.source_id = ol.id
JOIN ingredients i ON i.id = sm.ingredient_id
WHERE sm.lot_no = 'PO-0512'
  AND i.name = '雞胸肉'
  AND o.opened_at BETWEEN '2026-05-26 19:00+08' AND '2026-05-26 21:00+08';
```

### 1.2 過敏原標示（已落地）

`menu_items.allergens` 是 JSONB 陣列，存代碼，前端必須顯示：

```python
ALLERGEN_LABELS_ZH_TW = {
    "milk":      "乳製品",
    "egg":       "蛋",
    "wheat":     "小麥麩質",
    "peanut":    "花生",
    "tree_nut":  "堅果",
    "shellfish": "甲殼類",
    "fish":      "魚",
    "soy":       "大豆",
    "sulfite":   "亞硫酸鹽",
    "sesame":    "芝麻",
    "mango":     "芒果",  # 台灣消保法 11 項中包含
}
```

POS 點餐畫面 / 顧客菜單必須顯示這些標籤。**菜單沒標示而顧客過敏，店家自負法律責任。**

### 1.3 即期/效期管理

`ingredients.expiry_date` 是日期欄。每天清晨 06:00 跑這個 query：

```sql
-- 即期警示：3 天內到期、庫存仍 > 0 的食材
SELECT i.name, i.lot_no, i.expiry_date,
       i.expiry_date - CURRENT_DATE AS days_left,
       COALESCE(SUM(sm.qty), 0) AS on_hand
FROM ingredients i
LEFT JOIN stock_movements sm ON sm.ingredient_id = i.id
WHERE i.expiry_date IS NOT NULL
  AND i.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '3 days'
  AND NOT i.deleted_at IS NOT NULL
GROUP BY i.id, i.name, i.lot_no, i.expiry_date
HAVING COALESCE(SUM(sm.qty), 0) > 0
ORDER BY i.expiry_date ASC;
```

過期當天自動寫 `stock_movements` with `movement_type='expiry'`，扣帳到耗損成本。

### 1.4 HACCP 溫度紀錄（Phase 2 補）

冰箱 / 冷凍庫 / 熱鏈每 4 小時紀錄一次溫度。Phase 2 加 `haccp_temperature_logs` 表。
**Phase 1 過渡期：紙本紀錄，每日掃描存檔。**

### 1.5 食安事件處置 SOP

```
顧客申訴 ──┐
食安局通報 ─┼─► 1. 通知店長（5 分鐘內）
員工發現 ──┘    2. 隔離涉嫌批次（lot_no 列入禁用清單）
                3. 跑 1.1 query 列出影響訂單
                4. 客服主動聯繫該批次顧客（透過 customers.phone / line_user_id）
                5. 24 小時內通報衛生局（食品安全衛生管理法 §7）
                6. 寫 `audit_log` action="food_safety.incident_reported"
                7. 5 天內提供書面報告
```

---

## 二、勞動基準法（已部分落地）

### 2.1 工時 4 段（已落地）

`time_clocks` 已拆 4 桶：regular / overtime_tier1 (1.34×) / overtime_tier2 (1.67×) / holiday (2.0×)。
clock-out 時呼叫 `labor_hours_classifier`（待 DevSwarm 跑出後接）自動填寫。

**硬規定：單日工時 > 12 小時 raise — 軟體層擋。** 法律上限 12 小時，超過要罰款。

### 2.2 排班規範

- 連續工作 ≤ 6 天，第 7 天必為例假或休息日
- 兩班之間 ≥ 11 小時
- 每月加班 ≤ 46 小時（或經工會同意 54 小時）

Phase 2 補 schedule validator。Phase 1 排班員工自己注意，店長每週 review。

### 2.3 請假類別（已落地）

`leave_requests.leave_type` 列舉了 9 種勞基法假別：特休 / 事假 / 病假 / 婚假 / 喪假 / 產假 / 生理假 / 公假 / 其他。每種扣薪規則不同：

| 假別 | 給薪 | 上限 |
|---|---|---|
| 特休 | 全薪 | 工作年資而定（半年 3 天 → 5 年 14 天 → 25 年 30 天） |
| 事假 | 不給薪 | 14 天/年 |
| 病假 | 半薪 | 一般 30 天/年；普通傷病住院 1 年 |
| 婚假 | 全薪 | 8 天 |
| 喪假 | 全薪 | 3-8 天（依親等） |
| 產假 | 全薪 / 50% | 8 週 |
| 生理假 | 半薪 | 3 天/月（其中 1 天不計入病假上限） |
| 公假 | 全薪 | 必要時數 |

薪資計算引擎 Phase 2 補。

### 2.4 勞檢來訪 SOP

- 隨身備好：勞工名冊、工時紀錄（從 `time_clocks` 匯出）、薪資明細、勞健保保費繳款證明
- 勞檢員問什麼答什麼，不主動延伸
- 接到罰單先回 repo 對 audit_log 紀錄該時段的所有工時是否異常

---

## 三、個人資料保護法（PDPA-tw）

### 3.1 蒐集告知（必貼）

POS / 線上訂位 / LINE 加好友 都必須有「個資告知」彈窗：

```
本店蒐集您的姓名、電話、訂單偏好，用於：
1. 訂位確認與通知
2. 行銷活動寄送（您可隨時取消）
3. 食安事件即時通知

蒐集期間：自加入會員起 5 年。
您有查詢、更正、刪除、停止行銷的權利，請聯絡：privacy@<您的網域>.com
```

### 3.2 顧客資料下載 / 刪除請求

`customers` 表已預留 `deleted_at` 軟刪除欄。SOP：
- 接到刪除請求 30 天內處理
- 軟刪除（`deleted_at = now()`）保留 5 年（國稅局需求），實際隱藏個資欄位
- 5 年期滿 hard delete + cascade（要寫 nightly job）

### 3.3 資料外洩通報

外洩 ≥ 500 筆且涉及財損 → 72 小時內通報該管機關（個資法 §12）。
每次有資料外洩疑慮立刻寫 `audit_log` action="pdpa.breach_suspected"，內容描述事件範圍。

---

## 四、發票（已部分落地）

### 4.1 流程（schema 已支援）

```
顧客結帳 ──► invoice_status='pending'
                │
            開立發票
                ▼
        invoice_status='issued'
                │
   ┌────────────┼─────────────┬──────────┐
   ▼            ▼             ▼          ▼
 voided     allowance       winner     redeemed
 (作廢)      (折讓 C0701)    (中獎)     (顧客領獎)
```

`Order.invoice_status` + `void_invoice_number` + `allowance_invoice_number` 全已建好。

### 4.2 開立期限

- 雲端發票（`invoice_media='cloud'`）：開立時即上傳財政部
- 紙本發票（`invoice_media='paper'`）：當月開立的下月 5 日前批次上傳
- 漏報罰：每張至少 1,500 元 + 滯報金

### 4.3 折讓單 C0701（顧客退貨）

顧客退貨/退款時要開折讓單，發票號碼填到 `allowance_invoice_number`：

```sql
UPDATE orders
SET invoice_status = 'allowance',
    allowance_invoice_number = 'D0001234'  -- 折讓單字軌
WHERE id = $1;
INSERT INTO audit_log (action, target_table, target_id, actor_id, after)
VALUES ('invoice.allowance_issued', 'orders', $1, $2,
        jsonb_build_object('allowance_no', 'D0001234'));
```

### 4.4 中獎發票

每兩個月一期。財政部公告中獎號碼，比對 `invoice_number` LIKE 中獎號碼末 N 碼：

```sql
-- 5-6 月期中獎號碼 12345678（特獎，末 8 碼全中 200 萬）
SELECT * FROM orders
WHERE invoice_status = 'issued'
  AND opened_at BETWEEN '2026-05-01' AND '2026-06-30'
  AND invoice_number LIKE '%12345678'
ORDER BY opened_at;
```

中獎顧客可掃載具兌獎；如帶有 `customer_id`，主動 LINE 通知（PR 機會）。

---

## 五、現金管理（已落地）

### 5.1 開店 SOP

```
07:30  店長開店、開電
07:35  打卡上班（POS clock-in）
07:40  開啟 cash_drawer_session:
       - opening_float = 5000 TWD（零錢備用金）
       - opened_by = <店長 id>
       系統自動印出『開店確認單』，店長簽名留存
07:45  收銀員打卡上班 + 接班備用金確認
08:00  營業
```

### 5.2 換班 SOP

換班一定要 close 舊 session + open 新 session：

```python
# 換班時收銀員必填
close_cash_drawer_session(
    session_id=...,
    closing_actual=Decimal("12350.00"),  # 實際數錢
    closed_by=<該收銀員>,
)
# 系統自動算 variance = closing_actual - expected
# 若 |variance| > 50 TWD → 觸發 audit_log + 店長覆核
```

### 5.3 變異（variance）紅旗

- ±50 元以內：正常
- ±50-200 元：店長 review
- > ±200 元 或 連續 3 天負 variance：通報老闆
- 同一收銀員每月變異總和 < -500：列高度關注名單

### 5.4 收店 SOP

```
22:00  最後一單結帳
22:10  close_cash_drawer_session for 各 register
22:15  核對信用卡簽單張數 vs order_payments.method='credit' 筆數
22:20  打卡下班
22:25  打烊
```

---

## 六、災難情境 SOP

### 6.1 POS 當機 / Wi-Fi 倒

**降級流程：紙本接單 → 隔日補登**

1. 取出緊急紙本訂單本（每店常備 2 本）
2. 手寫訂單號（從預留段：`OFFLINE-YYYYMMDD-NNN`）
3. 顧客電話/姓名/品項/總額/收款方式記紙
4. 服務恢復後逐筆補登：

```python
# Order 補登時要標記
order = Order(
    order_no="OFFLINE-20260526-001",
    opened_at=parse_iso("2026-05-26T19:23+08:00"),  # 紙本紀錄時間
    closed_at=parse_iso("2026-05-26T19:45+08:00"),
    notes="Offline recovered from paper #003. Original cashier: 阿明.",
    # ... 其餘照常
)
audit_log(action="order.offline_recovered", target_id=order.id,
          after={"paper_ref": "OFFLINE-20260526-001"})
```

Phase 2 補：`orders.is_offline_recovered: bool` + `original_paper_no: text` 結構化欄位。

### 6.2 颱風天判斷

**取消營業條件**（任一成立）：
- 中央氣象署發布陸上警報且預估近 6 小時暴風範圍內
- 縣市政府宣布停班停課
- 員工通勤路線超過 50% 影響

判斷後：
1. 公告：店面玻璃門 + Google Maps「臨時休息」+ LINE 官方帳號群發
2. 食材保存：未開封食材丟冷凍 / 冷藏 / 真空保存；開封過半的列報廢（寫 waste_event）
3. 員工：依照例假 / 颱風假規則處理（颱風假無強制給薪，依勞動部公告）

### 6.3 食安事件公關

24 小時內 3 動作：

1. **下架**：涉嫌品項立即停售（`menu_items.is_available = false`）
2. **致歉**：Google Maps 評論回覆模板 + LINE 群發致歉信
3. **賠付**：影響顧客主動聯繫，提供退費或補償。寫 `order_discounts.kind='allowance'` 並開折讓單

**禁止行為：**
- 刪除 Google Maps 負評（會被認為心虛）
- 在社群嗆顧客（絕對失分）
- 私下封口（違反食安法 §7 強制通報義務）

### 6.4 火災 / 跳電

- 火災：人員撤離 → 119 → 不要關電（電腦資料還在跑）→ 翌日從備份還原
- 跳電：USP 保 30 分鐘 → 結帳中訂單 commit / rollback → 紙本接單 → 復電後比對

備份策略：Phase 2 上線時設定 Postgres pg_dump 每日 03:00 跑 → S3-compatible 留 30 天。
Phase 1 過渡：每週 docker exec 跑 pg_dump 落地到外接硬碟。

---

## 七、年度合規行事曆

| 時間 | 事項 | 對應欄位 / 流程 |
|---|---|---|
| 每月 5 日前 | 上月紙本發票批次上傳 | `invoice_media='paper'` 全部 |
| 每月 10 日前 | 上月營業稅申報（書面或網路） | `mv_daily_pnl` 月匯總 |
| 每兩月 25 日 | 統一發票中獎開獎 → 兌獎宣傳 | 4.4 query + LINE 推送 |
| 每季末 | 折讓單對帳 | `allowance_invoice_number` 全部 |
| 4 月底 | 綜所稅申報 | 員工 + 老闆個人 |
| 5 月底 | 營所稅申報 | 公司 |
| 每年 1 月 | 勞健保費率調整 | `employees` 薪資更新 |
| 每年 12 月 | 特休天數更新（依年資） | `leave_requests` 上限調整 |
| 颱風季（6-10 月） | 物料庫存壓力測試 | 確認備案 |
| 食藥署稽查（不定期） | HACCP 紙本紀錄 | 1.4 紀錄本 |
| 勞檢（不定期） | 工時 / 薪資 / 排班 | 2.4 SOP |

---

## 八、責任分配建議

| 角色 | 責任 |
|---|---|
| **指揮官（老闆）** | 全部風險最終承擔；月底 review audit_log 異常 |
| **店長** | 每日開閉店 SOP；現金 variance 異常處理；員工排班合規 |
| **主廚** | 食安：1.1-1.5 落地；過敏原標示維護 |
| **收銀** | 4.1-4.3 發票流程；5.1-5.4 現金 |
| **DevSwarm + 工程** | Schema 維護；nightly job 自動化；audit_log 寫入點覆蓋率 |

---

## 九、什麼東西寫死進 schema 了，什麼還待做

### ✅ 已在 schema / 程式碼支援
- 食材批號追溯（ingredients.lot_no、stock_movements.lot_no）
- 過敏原標示（menu_items.allergens jsonb）
- 食材效期（ingredients.expiry_date）
- 工時 4 桶（time_clocks）
- 請假 9 類（leave_requests.leave_type）
- 發票生命週期 6 狀態（orders.invoice_status）
- 折讓 / 作廢欄位（allowance_invoice_number / void_invoice_number）
- 現金備用金 + variance（cash_drawer_sessions）
- 審計日誌（audit_log）— 含 DB-level RULE 不可篡改

### ⏳ 待 Phase 2
- HACCP 溫度紀錄表
- 排班合規 validator
- 薪資 / 加班費 自動計算引擎
- nightly job（即期警示、變異警示、特休更新）
- pg_dump 自動備份

### 📋 紙本流程（Phase 1 過渡，Phase 2 數位化）
- HACCP 溫度紀錄本
- 紙本訂單本
- POS 當機補登流程
- 顧客致歉信模板
- 個資告知文案
