# 綠界 ECPay 串接

## 環境

| | stage（開發預設） | production |
|---|---|---|
| 結帳網址 | payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5 | payment.ecpay.com.tw/Cashier/AioCheckOut/V5 |
| MerchantID | 2000132（公開測試商店） | 申請後取得 |
| HashKey/IV | 5294y06JbISpM5x9 / v77hoKGq4kWxNNIS（公開） | 商店後台取得 |

切換：`wrangler secret put ECPAY_MERCHANT_ID / ECPAY_HASH_KEY / ECPAY_HASH_IV`，
`wrangler.toml` 的 `ECPAY_ENV` 改 `production` 後 `make deploy-worker`。

## stage 測試方式

1. 網站上登入 → 首頁點「購買」→ 導到綠界 stage 結帳頁
2. 信用卡：卡號 `4311-9522-2222-2222`、有效期任意未來、CVV `222`、OTP 任意
3. ATM：取號後回到本站 → 會員中心顯示虛擬帳號 → 綠界 stage 後台「模擬付款」觸發入帳 webhook
4. 驗證：會員中心方案徽章變綠、`payments.status='paid'`、`subscriptions` 有效期 +30 天
5. 冪等驗證：重放同一份 webhook payload（curl 重送 form data），訂閱不得重複展延

## CheckMacValue（簽章）

實作在 `worker/src/ecpay.js`，SHA256（EncryptType=1），流程：
排序參數（不分大小寫字典序）→ `HashKey=..&k=v..&HashIV=..` → encodeURIComponent
→ 全小寫 → .NET UrlEncode 相容替換（`%20`→`+` 等）→ SHA256 → 大寫。
單元測試在 `worker/test/ecpay.test.js`（不變量）；**最終正確性以 stage 實測為準**
（簽錯時綠界回 `10200073 CheckMacValue Error`）。

## Webhook（/api/ecpay/return）

- 必回純文字 `1|OK`，否則綠界會重送（也因此 handler 必須冪等）
- `RtnCode=1` 付款成功；`RtnCode=2` ATM 取號成功（未付款）；其他視為失敗
- ATM 是兩段式：取號 webhook（存虛擬帳號）→ 用戶轉帳 → 1-3 工作天後入帳 webhook
- 開通邏輯 `activateSubscription()`：同方案未到期 → 展延；否則新開 30 天

## 正式商店申請（Ivan 的待辦，與開發平行）

1. 需要公司或行號統編（個人賣家額度低且不能開電子發票，不建議正式用）
2. 申請綠界「全方位金流」，啟用信用卡 + ATM
3. 商店審核過後在後台拿正式 MerchantID / HashKey / HashIV
4. 電子發票：加購綠界電子發票模組（B2C 開立可全自動）— 線上收費依法要開發票
5. 綠界後台設定 ReturnURL 網域白名單（填 Worker 網址）
