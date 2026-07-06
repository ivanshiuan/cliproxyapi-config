# 16 — LINE 官方帳號版位設計（周霸虎老火鍋）

> 這份是「LINE 官方帳號」整套版位、文案、圖文選單、圖文訊息、菜單介紹、官網連結放置點的**可直接套用素材包**。
> 搭配檔案：`restaurant_api/line_assets/`（richmenu 設定、Flex 訊息 JSON、選單視覺稿）。

---

## 0-A. 現在馬上能用（只需要 `BASE_URL` 這一個值）

開幕輪盤（掃碼→LINE→抽獎→領券→綁會員）已經上線可用，不需要等官網做好。用這兩個檔案：

| 檔案 | 內容 |
|---|---|
| `richmenu_launch.json` + `richmenu_launch_mockup.html` | 2 格圖文選單：🎡 開幕輪盤抽獎 ／ 🎁 我的獎品錢包（兩格連到同一頁，該頁面已內建會員錢包顯示） |
| `flex_welcome_launch.json` | 加好友歡迎訊息，只有一個「🎡 立即抽獎」按鈕，沒有會 404 的死連結 |

裡面的 `BASE_URL` 只有一個值要換：**`https://chouhutiger.onrender.com`**（Render 部署網址，已確認上線）。

連結用的是 `BASE_URL/demo/campaign/grand-open`——固定 slug，不是資料庫隨機 UUID，換部署環境／重建資料庫都不用重印 QR code。

---

## 0-B. 之後官網做好再升級（目前是佔位，先別上架）

`richmenu.json`（6 格滿版）、`flex_campaign.json`、`flex_menu.json`、`flex_membership.json` 這幾份還留著「招牌菜單／線上訂位／會員／點餐／官網」等 5 個按鈕，指向 `OFFICIAL_SITE` 或 `/menu` `/booking` `/member` `/order`——**這些頁面現在都還不存在**，上架會變成客人點了 404。等你有這些頁面（或決定不做，只用同一個 App 擴充）再套用下面 3 個值：

| 變數 | 目前佔位 | 你要給我 |
|---|---|---|
| `OFFICIAL_SITE` | `https://your-domain.tw` | 官網網址 |
| 門市資訊 | `【地址】/【電話】/【營業時間】` | 真實地址、訂位電話、營業時間 |
| 菜單 | 下方第 6 節草稿 | 真實招牌菜＋價格（先用草稿可改） |

---

## 1. 帳號基本門面（先設定這些）

| 版位 | 建議內容 |
|---|---|
| **帳號名稱** | 周霸虎老火鍋 |
| **狀態消息**（名稱下一行小字） | 🔥 開幕輪盤天天抽・加入會員享專屬優惠 |
| **大頭貼** | 品牌 LOGO（深紫底 #1b1033＋金字，與輪盤同色系） |
| **封面圖** | 招牌鍋物照＋「盛大開幕 GRAND OPEN」字樣 |
| **基本資料** | 地址、電話、營業時間、`OFFICIAL_SITE` 官網連結 |
| **加入好友連結** | `@763yjise`（或 `https://lin.ee/xxxx` 短網址） |

---

## 2. 歡迎訊息（加好友自動跳出）— 圖文 + 連結

加好友後**立刻**送兩則：第一則 Flex 歡迎卡（圖＋按鈕），第二則純文字導引。

**第一則：Flex 歡迎卡** → 檔案 `line_assets/flex_welcome.json`

**第二則：純文字**
```
🔥 歡迎加入【周霸虎老火鍋】！

你已經是我們的會員了 🎉
現在就試手氣，開幕輪盤天天抽：
🎡 立即抽獎 ▶ BASE_URL/demo/?campaign=019eea5e-c121-79c3-9046-1d4bc52a3fa8

🍲 看招牌菜單 ▶ OFFICIAL_SITE/menu
📅 線上訂位 ▶ OFFICIAL_SITE/booking
🏠 品牌官網 ▶ OFFICIAL_SITE

📍 【地址】
☎️ 【電話】
🕙 【營業時間】

下方選單隨時點，吃火鍋找霸虎就對了！
```

---

## 3. 圖文選單（Rich Menu）— 核心版位，6 格

> 尺寸 **2500 × 1686 px**（大版）。視覺稿：`line_assets/richmenu_mockup.html`。
> 點擊區設定：`line_assets/richmenu.json`（已切好 6 格座標）。

```
┌───────────────┬───────────────┬───────────────┐
│ 🎡            │ 🍲            │ 📅            │
│ 開幕輪盤抽獎   │  招牌菜單      │  線上訂位      │
│ 天天抽大獎     │  看菜色/價格   │  快速訂位      │
├───────────────┼───────────────┼───────────────┤
│ 🎁            │ 🛵            │ 🏠            │
│ 我的會員/集點  │  線上點餐外帶  │  品牌官網      │
│ 看獎品錢包     │  免排隊        │  最新消息      │
└───────────────┴───────────────┴───────────────┘
```

| 格 | 圖示 | 標題 | 點下去連到 | 動作型態 |
|---|---|---|---|---|
| 1 | 🎡 | 開幕輪盤抽獎 | `BASE_URL/demo/?campaign=019eea5e-c121-79c3-9046-1d4bc52a3fa8` | uri |
| 2 | 🍲 | 招牌菜單 | `OFFICIAL_SITE/menu` | uri |
| 3 | 📅 | 線上訂位 | `OFFICIAL_SITE/booking` | uri |
| 4 | 🎁 | 我的會員／集點 | `OFFICIAL_SITE/member` | uri |
| 5 | 🛵 | 線上點餐外帶 | `OFFICIAL_SITE/order` | uri |
| 6 | 🏠 | 品牌官網 | `OFFICIAL_SITE` | uri |

> 配色：底 `#1b1033`、分隔線 `#3a1d6e`、主標金 `#ffd34e`、強調粉 `#ff5d8f`、白字 `#f4f0ff`，與抽獎輪盤同一視覺系統。

---

## 4. 圖文訊息（群發用）— 3 張主打 Flex

放在「主頁／群發」輪播，也可單獨推播。檔案在 `line_assets/`：

### 4-1 開幕活動卡 `flex_campaign.json`
- Hero 圖：開幕輪盤主視覺
- 標題：🎡 開幕輪盤・天天抽
- 內文：免單四人套餐、和牛、招待飲料…每日一抽
- 按鈕：**【立即抽獎】**(連抽獎頁) ＋ **【看官網】**(連 `OFFICIAL_SITE`)

### 4-2 招牌菜單卡（輪播）`flex_menu.json`
- 4 張橫向輪播，每張：菜色圖＋名稱＋價格＋「線上訂位」按鈕
- 每張 footer 都放 **官網連結** `OFFICIAL_SITE/menu`

### 4-3 會員集點卡 `flex_membership.json`
- 說明集點/兌換規則
- 按鈕：**【我的錢包】**(會員頁) ＋ **【官網看更多】**

---

## 5. 關鍵字自動回應（顧客打字就回）

| 顧客輸入 | 自動回 |
|---|---|
| `菜單` / `價格` | 推 `flex_menu.json` ＋「完整菜單 ▶ OFFICIAL_SITE/menu」 |
| `訂位` / `訂位電話` | 「📅 線上訂位 ▶ OFFICIAL_SITE/booking ☎️ 電話訂位【電話】」 |
| `抽獎` / `輪盤` | 「🎡 開幕天天抽 ▶ BASE_URL/demo/?campaign=019eea5e-c121-79c3-9046-1d4bc52a3fa8」 |
| `地址` / `怎麼去` | 「📍【地址】｜Google 地圖 ▶ 【地圖連結】」 |
| `營業時間` / `幾點` | 「🕙【營業時間】」 |
| `官網` / `更多` | 「🏠 品牌官網 ▶ OFFICIAL_SITE」 |
| `會員` / `集點` | 「🎁 會員錢包 ▶ OFFICIAL_SITE/member」 |

---

## 6. 菜單介紹內容（草稿・請改成真實菜色與價格）

> 這份只是讓素材先有東西，**請用你的真實菜單覆蓋**。

**🍲 招牌鍋底**
- 霸虎麻辣鍋（招牌）— NT$xxx
- 老火鍋・牛骨高湯 — NT$xxx
- 養生菌菇鍋 — NT$xxx
- 韓式泡菜鍋 — NT$xxx

**🥩 嚴選肉品**
- 澳洲和牛盤 — NT$xxx
- 霜降牛 — NT$xxx
- 梅花豬 — NT$xxx
- 去骨牛小排 — NT$xxx

**🦐 海鮮 / 手工**
- 活蝦 / 蛤蜊 / 鮮魚片 — NT$xxx
- 手工花枝漿 / 滑 — NT$xxx
- 鴨血豆腐 — NT$xxx

**🥬 蔬菜 / 飲品 / 甜點**
- 時蔬拼盤 — NT$xxx
- 古早味紅茶 / 冬瓜茶（招待，抽獎可中）
- 手工冰淇淋 — NT$xxx

---

## 7. 「官網連結放置點」總清單（你說全部塞進去 → 這些都放了）

1. 帳號基本資料欄
2. 歡迎 Flex 卡按鈕
3. 歡迎純文字訊息
4. 圖文選單第 6 格（品牌官網）
5. 開幕活動卡「看官網」按鈕
6. 菜單輪播每張 footer
7. 會員卡「官網看更多」按鈕
8. 關鍵字「官網／菜單／會員」自動回應

---

## 8. 怎麼把這些設定上去

- **圖文選單 / 歡迎訊息 / 關鍵字** → LINE Official Account Manager 後台（`manager.line.biz`）手動貼上即可，不用寫程式。
- **Flex 圖文訊息** → 兩種方式：
  1. 後台「圖文訊息」用視覺編輯（簡單版）。
  2. 用 Messaging API 推 Flex JSON（完整版，本資料夾 JSON 可直接用，記得先把 `BASE_URL` 換成 `https://chouhutiger.onrender.com`）。範例：
     ```bash
     curl -X POST https://api.line.me/v2/bot/message/broadcast \
       -H "Authorization: Bearer $LINE_CHANNEL_ACCESS_TOKEN" \
       -H "Content-Type: application/json" \
       -d @restaurant_api/line_assets/flex_welcome_launch.json
     ```
- **圖文選單圖片**：把 `richmenu_launch_mockup.html`（現在馬上上架用）或 `richmenu_mockup.html`（官網做好再用的 6 格版）用瀏覽器開→截圖存成對應尺寸 PNG，上傳後台即可。

> ⚠️ Flex 訊息的 Hero 圖需要 HTTPS 圖片網址。圖片可放官網／圖床，網址填進各 JSON 的 `url` 欄位（目前是佔位）——`flex_welcome_launch.json` 沒有這個問題，已拿掉 hero 圖，只保留純文字＋抽獎按鈕。

---

## 9. 門口海報：不用開 LINE Developers 也能先印

如果只是要一張門口貼的海報（不透過 LINE 圖文選單），後端已經有現成的印刷端點，直接開瀏覽器就有結果，不用寫任何設定檔：

```
https://chouhutiger.onrender.com/campaigns/by-slug/grand-open/poster
```

打開就是一張排版好的 A4 海報（含內嵌 QR、活動名稱、標語），瀏覽器 `Ctrl/Cmd+P` 存成 PDF 或直接印。想換品牌色/標語可以加參數，例如：

```
https://chouhutiger.onrender.com/campaigns/by-slug/grand-open/poster?brand=周霸虎老火鍋&tagline=盛大開幕%20天天抽大獎&primary=%23ff5d8f&accent=%23ffd34e
```

同一組 slug 也有純 QR 版本（`qr.svg`），要自己排版套版可以用：

```
https://chouhutiger.onrender.com/campaigns/by-slug/grand-open/qr.svg
```
