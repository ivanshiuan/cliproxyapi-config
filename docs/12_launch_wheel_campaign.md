# 12 — 開幕引流輪盤抽獎 (Wheel-Spin Lottery Campaign)

> 門口擺 QR Code → 掃碼進輪盤 → 抽獎即加入會員 → 抽到券 → 來店核銷吃飯。
> 這份是後端引擎的設計與操作說明。前端輪盤頁另案，串接本文的 API 即可。

## 一句話

一個「每日抽獎、券進錢包、來店核銷」的開幕引流引擎：會員每天可抽一次，
抽中的獎品變成有時效的兌換券累積在錢包，每次來店只能核銷一張（通常挑最大獎），
最大獎（免單四人 / 雙人套餐）用機率權重 + 總量上限控管稀有度。

## 資料模型（4 張表）

| 表 | 角色 | 可變性 |
|---|---|---|
| `marketing_campaigns` | 活動本體 + 設定（每日抽次數、券效期規則、每日訊息） | 可改 / 軟刪 |
| `campaign_prizes` | 輪盤獎項（權重、價值、菜單品項、總量/每日上限） | 可改 / 軟刪 |
| `campaign_spins` | 每次抽獎的 append-only 日誌（每日限抽的計數來源） | append-only |
| `campaign_vouchers` | 抽中的券（進會員錢包，核銷一次） | 狀態機 active→redeemed/expired/void |

## 三個關鍵規則（已選定）

1. **券效期 = 固定起算 + 固定天數**
   `valid_from = 抽中日 + voucher_start_offset_days`、
   `valid_until = valid_from + voucher_validity_days`（預設 offset 0、效期 14 天）。
   想做「開幕日才開放使用」就把 `starts_at`/offset 設成開幕日。

2. **每日抽一次 + 每次來店核銷一張**
   `daily_spin_limit`（預設 1）控管每位會員每天可抽次數（以 Asia/Taipei 日界）。
   核銷時若同會員同活動「今天」已核銷過一張，第二張會被擋（409）。
   DB 端有 `uq_vouchers_one_redeem_per_day` 部分唯一索引當競態防線。

3. **大獎稀有度 = 低權重 + 總量上限**
   抽獎是加權隨機（`weight / Σweight`）。大獎給小 `weight` 再加小 `total_quota`
   （整檔上限）或 `daily_quota`（每日上限）。額度抽完 → 該獎退出抽池；
   若全部退出則該次為「沒中」（不發券）。
   「銘謝惠顧」段 = `menu_item_id=None` 且 `value_estimate=0`，會記錄抽獎但不發券。

## API 地圖（`/campaigns`）

設定端（營運）
- `POST /campaigns` 建活動（草稿；上線改 `status=active`）
- `GET/PATCH /campaigns/{id}` 查 / 改（含狀態 draft→active→paused→ended）
- `POST/GET /campaigns/{id}/prizes`、`PATCH .../prizes/{pid}` 維護獎項

玩家端
- `GET /campaigns/{id}/wheel` 公開輪盤版面（前端渲染用；不含機率）
- `POST /campaigns/{id}/spin` 抽一次。用 `customer_id` 或 `line_user_id`/`phone`
  指定會員；首抽自動建會員（加入會員 hook）。回傳獎項 + 券 + 當日訊息，
  並對有 `line_user_id` 的會員推播 `daily_message`（彈跳訊息）。

錢包 / 櫃台
- `GET /campaigns/{id}/vouchers?customer_id=` 會員錢包（價值高→低，最大獎在最上）
- `GET /campaigns/{id}/vouchers/by-code/{code}` 以兌換碼查券（掃碼）
- `POST /campaigns/{id}/vouchers/{vid}/redeem` 核銷（每日限一張）

## 設定範例（開幕檔）

```jsonc
// 1) 建活動：每天抽 1 次、抽中當天起算、效期 14 天、每日彈跳訊息
POST /campaigns
{ "name": "開幕輪盤", "slug": "grand-open", "status": "active",
  "daily_spin_limit": 1, "voucher_start_offset_days": 0,
  "voucher_validity_days": 14, "daily_message": "今天也來抽一波！本週主廚菜 8 折" }

// 2) 獎項：大獎稀有（weight 1 + 整檔只給 3 份），常見小獎權重高
POST /campaigns/{id}/prizes
{ "name": "免單四人套餐", "weight": 1, "value_estimate": "3200",
  "menu_item_id": "...", "total_quota": 3 }
POST /campaigns/{id}/prizes
{ "name": "招待小菜一份", "weight": 60, "value_estimate": "60", "menu_item_id": "..." }
POST /campaigns/{id}/prizes
{ "name": "銘謝惠顧", "weight": 39, "value_estimate": "0" }   // 沒中段
```

## 併發與正確性

- `spin` 對活動列上 `FOR UPDATE` 鎖，序列化同活動的抽獎 → 每日計數與額度扣減無競態
  （開幕單店、量不大，這個取捨最簡單也最穩）。
- `redeem` 對券列上鎖，核銷的「每日一張」再靠唯一索引兜底。
- 金額一律 `Decimal` / `Numeric(14,4)`；時間 DB 存 UTC、日界用 Asia/Taipei。
- 稽核：建活動 / 發券 / 核銷都走 `audit_service.audit()`。

## 還沒做（之後可接）

- 夜間 job 把過期未核銷的券批次轉 `expired`（目前是核銷時惰性轉 + 錢包讀時按日期判斷）。
- LINE 真實推播：目前走 `StubLineMessenger`，填 `LINE_CHANNEL_ACCESS_TOKEN` 即切真實通道。
- 前端輪盤頁 / QR 產生器（本檔只交付後端引擎）。
