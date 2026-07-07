# 17 — 開幕輪盤上線收尾復盤（Render 部署 + LINE 綁定前）

> 涵蓋範圍：從「Render 部署 `exit 127`」到「LINE LIFF/圖文選單綁定前」這一段收尾工作。
> 前情提要（248 抽/40 核銷/9 對抗壓測）見 `docs/16_wheel_uxtest_retrospective.md`。
> 這份記錄實際發生的問題、根因、修法、驗證方式，以及誠實列出還沒完成、且**不是工程問題**的部分。

---

## TL;DR

| 面向 | 結論 |
|---|---|
| **Render 部署** | ✅ 修好 `exit 127`（根因是 `dockerCommand` 多行 shell 字串被 YAML 解析器切斷），改用 bake 進 image 的 `start.sh`，已確認 `/health/live` 上線 |
| **本地端完整壓測** | ✅ 120 人旅程 + 40 核銷 + 9 對抗測試，全通過（跟 docs/16 同一套流程重跑一次確認迴歸） |
| **環境間 UUID 不穩定** | ✅ 新增 slug-based 路由（`by-slug/{slug}`、`/demo/campaign/{slug}`），QR/海報網址不再綁死某一次資料庫重建產生的隨機 id |
| **LINE 素材死連結** | ✅ 原本 6 格圖文選單有 5 個連到不存在的官網頁面（會 404），改做「現在能用」的 2 格精簡版 |
| **真人瀏覽器測試（新增）** | ✅ 用無頭瀏覽器（非 API call）實際點擊「抽獎」按鈕、驗證畫面渲染，抓到一個真的 404（favicon，已修） |
| **卡住的部分** | 🔴 LIFF 建立 / 圖文選單上架 / 真人手機掃碼驗證——**確認**（非猜測）是這個執行環境的網路政策擋掉所有外部網域，且 OAuth 登入無法在非互動 session 執行 |

---

## 一、Render `exit 127` 根因與修法

**根因**：`render.yaml` 原本用 `dockerCommand: sh -c "..."` 包一段多行 shell 腳本。Render 解析多行 YAML 折疊字串時，shell 引號被當純文字切開，`sh -c` 實際收到的是斷裂的片段（例如只有 `"cd` ），導致找不到指令、exit 127。

**修法**：把整段啟動邏輯（migrate → seed → uvicorn）搬進 `restaurant_api/start.sh`，`COPY` 進 image、`chmod +x`，Dockerfile `CMD` 直接指向這支腳本。render.yaml 不再需要 `dockerCommand`，消除了 YAML 折疊字串這個故障源頭。

**驗證**：使用者截圖 `https://chouhutiger.onrender.com/health/live` 回 `{"status":"alive",...}`。另外在本地端完整重跑一次「migrate → seed → 啟動 → 120 人壓測」確認同一套程式碼在乾淨環境下行為一致。

---

## 二、環境間 campaign_id 不穩定 → slug 導向

**問題**：`MarketingCampaign.id` 是 UUIDv7，每個資料庫獨立產生（本地開發、Render prod 各自不同）。原本 QR code / LINE 圖文選單設計是把 `campaign_id` 寫死進網址——換一個部署環境、或資料庫重建，所有印出去的海報、設定好的圖文選單全部失效。

**修法**：
- `campaigns_service.get_campaign_by_slug()`：用建活動時取的固定 `slug`（例如 `grand-open`）查活動，不用 id
- `GET /campaigns/by-slug/{slug}/qr.svg`、`/poster`：QR、海報產生器的 slug 版本
- `GET /demo/campaign/{slug}`：307 導向 `/demo/?campaign=<真實id>`，保留品牌參數（`liff`/`brand`/`tagline`…）

**驗證**：8 個新 pytest（qr/poster by-slug 正確解析、404 處理、redirect 保留 query params），加上既有 32 個 campaigns 測試，40/40 全過；ruff + pyright clean。

---

## 三、LINE 素材：從「看起來完整」到「上線不會 404」

**發現的問題**：原始 6 格圖文選單、歡迎 Flex 卡設計得很完整（招牌菜單／線上訂位／會員／點餐／官網），但這些頁面在後端**完全不存在**（沒有 `/menu`、`/booking`、`/member`、`/order` 路由）。如果照原設計直接上架，客人點了 5 個按鈕會看到 404——比沒有選單還糟。

**修法**：不是把佔位網址硬填一個假網址糊弄過去，而是做一個誠實的「現在能用版」：
- `richmenu_launch.json`／`.png`：2 格，🎡 抽獎 ／ 🎁 我的獎品錢包（兩格連到同一頁——該頁面已經內建會員錢包顯示，不是真的兩個功能）
- `flex_welcome_launch.json`：拿掉會 404 的 hero 圖跟「逛官網」按鈕，只留一個真的可以按的「立即抽獎」

原本的 6 格滿版（`richmenu.json`）保留作為「官網頁面做好之後」的升級版，文件裡明確標註兩者差異，不是刪掉。

**額外做的**：用無頭瀏覽器把兩份視覺稿渲染成 LINE 要求的精確像素 PNG（2500×843、2500×1686，`scripts/render_richmenu_png.py`），使用者不用自己截圖裁切，也不用擔心手動截圖差幾個像素被 LINE 後台拒絕上傳。

---

## 四、真人瀏覽器 E2E 測試（這次新增，之前只測過 API 層）

之前所有驗證（含 docs/16 的 248 次壓測）都是**直接打 API**（`httpx`/`curl`），沒有真的用瀏覽器渲染過頁面、點過按鈕。這次額外用 Playwright 開一個模擬 iPhone 13 的瀏覽器分頁，實際：

1. 開啟 `/demo/?campaign=...`，確認畫面渲染（輪盤、預設會員欄位）
2. 點擊「抽獎一次」按鈕（真的觸發 DOM click，不是呼叫 API）
3. 等待動畫跑完，確認畫面顯示結果文字、錢包卡片正確顯示
4. 監聽瀏覽器 console，抓執行期間所有錯誤

**發現**：`favicon.ico` 404（瀏覽器自動探測，非功能性錯誤）。已加一行 inline SVG favicon 修掉，40 個相關 pytest 全過後 commit。

**沒發現**：抽獎流程本身（點擊→動畫→結果→錢包更新）在真實瀏覽器 DOM 層面沒有任何 JS 錯誤或渲染問題。唯一的 console 錯誤是 LIFF SDK（`static.line-scdn.net`）載入失敗——這是**這個測試環境**的網路政策擋掉外部網域造成的，不是程式問題；在客人的真實手機上，這個網域是可以連的。

---

## 五、卡住的部分：為什麼不是工程問題

以下三件事在多次嘗試後**確認**（非假設）無法由這個 session 自動完成：

1. **LINE LIFF 建立**（LINE Developers Console）
2. **圖文選單 / 歡迎訊息上架**（LINE Official Account Manager）
3. **真人手機掃碼驗證**

驗證過程：
- 直接 `curl`/`WebFetch` 打 `api.line.me`、`developers.line.biz`、`manager.line.biz`、`access.line.me`、`chouhutiger.onrender.com` 全部回 `403`
- 讀這個沙盒環境自己的網路政策文件（`/root/.ccr/README.md`），上面明文：「403/407 是組織政策拒絕，不要重試、不要繞過去」
- 即使網域可連，LINE 的帳號登入是互動式 OAuth（含兩步驟驗證），系統本身已提示「非互動 session 無法執行 OAuth 流程」
- 手機掃碼需要物理裝置，AI agent 沒有手機

這三件事不是「還沒試」，是這個執行環境的硬性邊界。所有可以在沒有使用者登入的情況下完成的準備工作（部署、測試、素材、文件、網址預填）都已做完並驗證。

---

## 六、待使用者完成的最後三步（COMMANDER_HANDOFF.md 有完整版）

1. LINE Developers Console 開 LIFF app → 把 LIFF ID 回報，我在 1 分鐘內接進所有素材、重新產生 PNG、push
2. LINE Official Account Manager 上傳 `richmenu_launch.png`、設定歡迎訊息（`flex_welcome_launch.final.json`，只剩地址/營業時間兩處要填）
3. 真人手機掃碼，走一次「加好友→抽獎→看結果→查錢包」確認順暢
