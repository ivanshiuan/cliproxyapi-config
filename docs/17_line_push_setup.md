# LINE 推播設定教學

> 目標：讓系統能真的發 LINE 訊息（抽中獎通知、會員升級/生日/召回）。
> 程式我已經全部寫好接好了 —— 你只需要做「**註冊帳號 + 拿一把鑰匙（token）**」，貼進設定就會動。
> 拿到 token 後驗證：`make line-check USER=<你的line_user_id>`

---

## 全貌（90 秒看懂）

```
你做：註冊 LINE 官方帳號 → 開 Messaging API → 複製「Channel access token」
              │
              ▼
貼進 .env：  LINE_CHANNEL_ACCESS_TOKEN=xxxxx
              │
              ▼
系統自動切換成真實推播（原本是測試用的假發送）
```

只需要 **一個值**：`LINE_CHANNEL_ACCESS_TOKEN`。Secret 之後做雙向互動（webhook）才需要，現在不用。

---

## Part A — 你要手動做的（只有你本人能做，需用你的手機/email 登入）

### 步驟 1：註冊 LINE 官方帳號（LINE Official Account）

1. 開 **LINE Official Account Manager**：<https://manager.line.biz/>
2. 用你的 LINE 帳號登入 → 「建立帳號」。
3. 填店名、類別（餐飲）、地區（台灣）→ 建立。
   - 這就是客人會加好友、收到你訊息的那個「官方帳號」。

### 步驟 2：啟用 Messaging API

1. 在 Official Account Manager 內 → 右上「設定」→ 左側「Messaging API」。
2. 點「啟用 Messaging API」。
3. 它會要你選一個 **Provider**（開發者/公司名稱）；沒有就新建一個（填店名即可）。
4. 啟用完成後，它會把這個帳號連到 **LINE Developers Console**。

### 步驟 3：取得 Channel access token（最重要的那把鑰匙）

1. 開 **LINE Developers Console**：<https://developers.line.biz/console/>
2. 進到剛剛那個 Provider → 點你的 channel（Messaging API 那個）。
3. 切到上方「**Messaging API**」分頁。
4. 找到「**Channel access token (long-lived)**」→ 按「**Issue**（發行）」。
5. 複製那一長串字（這就是 `LINE_CHANNEL_ACCESS_TOKEN`）。

> ⚠️ 這把 token 等於你帳號的發訊權限，**不要貼到公開的地方、不要 commit 進 git**。

### 步驟 4（重要觀念）：誰能收到訊息？

LINE 規定：**只能發給「已經加你官方帳號好友」的人**。
我們系統的設計剛好吻合 —— 客人掃 QR 玩輪盤時用的就是他的 LINE 身分，只要他有加你官方帳號好友，就收得到中獎通知。所以記得在門口/菜單放「加 LINE 好友」的引導。

---

## Part B — 程式設定（我已幫你做好，你只要貼 token）

### 把 token 貼進設定

**本機 / 自架伺服器**：編輯專案根目錄的 `.env`，加一行：
```bash
LINE_CHANNEL_ACCESS_TOKEN=這裡貼你剛剛複製的長字串
```
（`.env.example` 已有註解範本可參考；`.env` 不會進 git。）

**雲端部署（Zeabur / Docker 等）**：在平台的「環境變數」設定加一筆同名變數即可，不用改 `.env`。

> 系統會自動偵測：**有 token → 真實推播；沒 token → 測試模式（只記 log 不真的發）**。不用改任何程式碼。

### 驗證有沒有成功（一行指令）

先拿到「要發給誰」的 `line_user_id`（你自己的）。最簡單：把官方帳號加好友後，在 LINE Developers Console 的測試工具，或之後接 webhook 取得。拿到後：

```bash
make line-check USER=<你的line_user_id>
# 或：.venv/bin/python scripts/line_check.py <你的line_user_id>
```
- 成功 → 你的 LINE 會收到一則測試訊息，終端機印 `✅ 已送出`。
- 失敗 → 終端機會印出 LINE 回的錯誤碼與原因（token 錯/過期、user_id 不對、對方沒加好友）。

---

## 系統會在什麼時候自動推播？

接好 token 後，這些原本就寫好的地方會開始真的發 LINE：

| 時機 | 訊息內容 | 來源 |
|---|---|---|
| 客人抽獎當下 | 活動的「每日訊息」（抽中提示） | `campaigns_service.spin` |
| 每晚 04:15 | 會員升級 / 生日禮 / 沉睡召回 | `jobs/membership_lifecycle` |
| 訂單結帳 | 點數累積通知 | `orders_service` |
| 裂變 / 儲值 / UGC | 對應獎勵通知 | 各 service |

> 所有推播都是「**盡力送、送失敗不影響主流程**」—— 即使 LINE 掛了，抽獎、結帳照常完成，只是少一則通知。

---

## 常見問題

| 狀況 | 原因 / 解法 |
|---|---|
| 設了 token 還是沒發訊息 | 確認變數名是 `LINE_CHANNEL_ACCESS_TOKEN`（不是 RESTO_ 開頭）；重啟服務讓設定生效 |
| `line-check` 回 401 | token 錯或過期 → 回 Console 重新 Issue 一把 |
| `line-check` 回 400 / 對方收不到 | 該 `line_user_id` 沒加你官方帳號好友，或 id 不對 |
| 想發給「一群人」 | 系統已支援分眾（會員生命週期 job 會逐人發）；大量群發走 multicast，每批 500 人 |
| token 會過期嗎 | long-lived token 長期有效；外洩就回 Console 重發一把（舊的作廢） |

---

## 要我幫你「連 token 一起設好」嗎？

可以，但有個安全提醒：**token 是機密**。如果你願意，可以把 token 給我，我會：
1. 幫你寫進這個環境的 `.env`（已被 gitignore，不會進 git）。
2. 用 `scripts/line_check.py` 對你指定的 user_id 做一次真實發送測試，確認能通。

⚠️ 注意：**這個雲端開發環境是暫時的**，重開就清空 —— 所以這裡設的 token 只能用來「當場測試」。**正式上線**那把 token 還是要你親自貼到你的部署平台環境變數（Part B），那才是會長期運作的地方。
