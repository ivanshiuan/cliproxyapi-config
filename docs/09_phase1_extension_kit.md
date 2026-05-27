# 09 — Phase 1 延伸套件：KDS、訂位/候位、LINE 統一抽象

> 沿著 docs/08 食安/合規之後，第三批閉環：餐廳「前場」流程 + 跨模組通訊樞紐。
> 全部 schema-only 落地、邏輯層保留接口。Phase 2 寫業務邏輯時，schema 不用再改。

---

## 一、Kitchen Display System (KDS)

### 為什麼直接加在 `order_lines` 而不是另開 `kitchen_tickets` 表

權衡過：
- **加在 order_lines（採用）**：少一個表、查詢直接、簡單
- **另開 kitchen_tickets**：可支援「同一菜分送多站」（主廚房 + 醬汁站）

MVP 不會出現「一個品項分送 2 個站」的需求；連鎖、中大型餐廳到 Phase 4 再考慮升級成獨立表。

### 落地欄位（order_lines）

| 欄位 | 型別 | 用途 |
|---|---|---|
| `kitchen_station` | enum: kitchen / bar / dessert / counter | 路由：哪個站收單 |
| `kitchen_status` | enum: queued / cooking / ready / served / cancelled | 5 狀態 lifecycle |
| `sent_to_kitchen_at` | timestamptz | 進入 KDS 時點 |
| `cooking_started_at` | timestamptz | 廚師「開始做」按按鈕 |
| `ready_at` | timestamptz | 完成出餐口 |
| `served_at` | timestamptz | 服務生送到桌 |

延伸分析查詢（待 Phase 2 dashboard）：

```sql
-- 「過去 7 天平均出餐時間」by 站
SELECT kitchen_station,
       AVG(EXTRACT(EPOCH FROM (ready_at - sent_to_kitchen_at)) / 60) AS avg_minutes,
       COUNT(*) AS n
FROM order_lines
WHERE sent_to_kitchen_at > now() - INTERVAL '7 days'
  AND ready_at IS NOT NULL
GROUP BY kitchen_station
ORDER BY avg_minutes DESC;
```

「出餐超時」（> 20 分鐘）警示：

```sql
SELECT ol.id, mi.name, ol.kitchen_station,
       now() - ol.sent_to_kitchen_at AS waiting
FROM order_lines ol
JOIN menu_items mi ON mi.id = ol.menu_item_id
WHERE ol.kitchen_status IN ('queued', 'cooking')
  AND now() - ol.sent_to_kitchen_at > INTERVAL '20 minutes'
ORDER BY waiting DESC;
```

### Phase 2 待做
- KDS 前端介面（簡易 iPad/Android tablet web app）
- 「叫號完成」推播給服務生（手錶 / 手機）
- 出餐超時即時 alert 給店長
- 廚師個人 KPI（平均出餐時間、誤點率）

---

## 二、訂位 (Reservation) + 候位 (Walk-in Queue)

完整餐飲服務流：

```
候位 (queue) ─► 訂位 (reservation) ─► 入座 (seat) ─► 點餐 (order)
                                            ▲
                            Phase 1 從這裡開始；前段這次補上
```

### 兩張表並存（不合併的理由）

訂位 = 預先排程；候位 = 現場排隊。
- 訂位有未來時間欄、確認流程、訂金
- 候位是 FIFO 即時插入，沒有「未來時間」

合併會產生 30% 的 nullable 欄位 + 兩套邏輯擠在同一 status 機。分開乾淨。

### Reservation 生命週期

```
booked → confirmed → seated → completed
                          ↓
                      no_show （超過時段 15 分鐘未到自動轉）
                          ↓
                      cancelled （顧客主動取消）
```

`source` 欄是字串而非 enum，因為通路會增長：phone / line / google_reserve / inline / eztable / walk_in_promoted / ...

`deposit_amount` 欄：火鍋店等高客單常收訂金。Phase 2 接 LINE Pay / 街口時自動沖銷。

### WalkInQueueEntry 生命週期

```
waiting → called → seated
       ↓
   abandoned （叫號但未到 / 自行離開）
```

`queue_no` 欄是顧客拿到的票號（「A-23」），人類友善而非 UUID。

### 整合 LINE（Phase 2）

`Reservation.source = 'line'` + `customer_id` 已設 → 透過 `LineMessenger.push(line_user_id, ...)` 推送：
- T-1h 提醒
- 桌位準備好通知
- 候位「您前面剩 N 組，預估 X 分鐘」

---

## 三、LINE 統一通道（restaurant_api/integrations/line/）

### 為什麼需要這個抽象

台灣 F&B context 中 LINE 是「顧客 / 員工 / 行銷」三軸共用的管道：

| 軸 | 場景 |
|---|---|
| 顧客 | 訂位確認、候位叫號、食安通報、行銷推播、點數通知 |
| 員工 | 換班請求、打卡提醒、排班發布 |
| 行銷 | 分眾推播、活動通知、生日禮 |

若 40 個 router 各自 import LINE SDK，未來換 Telegram / 換訊息策略時要改 40 個地方。

### 三個操作就覆蓋所有用例

```python
class LineMessenger(ABC):
    async def push(self, line_user_id: str, message: LineMessage) -> None:
        """1-to-1 直接推送（已知 user_id）"""

    async def broadcast(self, audience: BroadcastAudience, message: LineMessage) -> int:
        """分眾推播；audience 用 tier / tag / explicit_user_ids 選取"""

    async def reply(self, reply_token: str, message: LineMessage) -> None:
        """webhook 60 秒 reply window 內回覆"""
```

### Phase 1 / Phase 2 切換

```python
m = get_messenger()  # auto-select
```

- 環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 為空 → `StubLineMessenger`（in-memory，測試友善）
- 環境變數有值 → `HttpLineMessenger`（Phase 2 寫好實作）

切換**不需改任何業務代碼**。Phase 1 全部 router 都用 `LineMessenger` 介面，等真實 LINE 商號開通就翻環境變數。

### 業務代碼用法（示例，Phase 2 真實長相）

```python
async def confirm_reservation(reservation: Reservation, m: LineMessenger):
    if not reservation.customer or not reservation.customer.line_user_id:
        return
    await m.push(
        reservation.customer.line_user_id,
        LineMessage(
            kind="text",
            text=f"您的訂位已確認：{reservation.reserved_for:%Y-%m-%d %H:%M}，{reservation.party_size} 位",
        ),
    )
```

### 測試模式

```python
# In a test:
from restaurant_api.integrations.line import StubLineMessenger

stub = StubLineMessenger()
await some_business_function(messenger=stub)
assert stub.sent_messages == [
    {"op": "push", "to": "U123abc", "message": LineMessage(...)},
]
```

`StubLineMessenger.sent_messages` 是 source of truth，不需要 mock SDK。

---

## 四、DevSwarm prompt 版本化（E7）

### 為什麼

當 PM/Architect/Coder/QA prompt 改動，所有後續任務都吃到新版本，無法回答「任務 X 用了哪版？」。

### 落地

`devswarm/prompts/_versions.py`：

```python
PM_PROMPT_VERSION       = "1.0.0"
ARCHITECT_PROMPT_VERSION = "1.0.0"
CODER_PROMPT_VERSION    = "1.0.0"
QA_PROMPT_VERSION       = "1.0.0"
```

`TelemetryMsg` 加 `prompt_version` 欄；每次節點呼叫自動寫入。
之後重 review run 時可從 `state["messages"]` 抓出版本。

### 何時 bump

| 變動 | 該 bump |
|---|---|
| 修錯字 / 排版 | PATCH (1.0.0 → 1.0.1) |
| 加新 AC 要求、收緊規則 | MINOR (1.0.0 → 1.1.0) |
| 重寫角色 / 翻倍長度 | MAJOR (1.0.0 → 2.0.0) |

bump MAJOR 時，在對應 prompt 檔頂加 CHANGELOG 段落說明動機。

---

## 五、Pyright type check（E8）

`pyproject.toml [tool.pyright]` 配置好 basic mode，包含 devswarm / restaurant_api / scripts。
CI workflow 加 `pyright` 步驟。
本地：`make typecheck`。

目前 0 errors。

---

## 六、本批新增表 / 欄一覽

| 變動 | 對應閉環 |
|---|---|
| order_lines + 6 KDS 欄位 + 1 index | F6 |
| 新表 `reservations` | F7 |
| 新表 `walk_in_queue` | F7 |
| `devswarm/prompts/_versions.py` | E7 |
| `TelemetryMsg.prompt_version` | E7 |
| `[tool.pyright]` + CI step | E8 |
| `restaurant_api/integrations/line/` | γ |

**總表數：25**（原 23 + 2）

---

## 七、剩下待補（誠實清單）

| 項目 | 排程 | 紙本/手動過渡 |
|---|---|---|
| KDS 前端（iPad） | Phase 2 | 廚房紙本印表機 |
| 訂位 LINE bot | Phase 2 | 電話訂位 |
| `HttpLineMessenger.push/broadcast/reply` 實作 | Phase 2 | StubLineMessenger 寫日誌 |
| 加盟資料治理 (F9) | T3 啟動前 | — |
| 品牌危機 SOP (F10) | 開店前 1 個月 | docs/08 §6 已含食安 24h |

E9 / E10 / F8 / F9 / F10 都在 docs/03_roadmap.md 已標 Phase 2-4。
