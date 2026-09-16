# 18 — BrowserAct（瀏覽器自動化）設定與使用

> 什麼時候用 BrowserAct：需要一個 AI agent 去**瀏覽網頁、點擊、填表單、處理登入流程、
> 過人機驗證、繞過 bot 偵測、或從網站抽結構化資料**時。它是比內建 WebFetch/curl 更強的
> 「會 render JavaScript + 會互動 + 會維持登入狀態」的瀏覽器。

---

## 這是什麼

- **技能**：`.claude/skills/browser-act/SKILL.md`（已進版控，每個 session 自動載入）。
- **CLI**：`browser-act`，用 `uv tool install browser-act-cli --python 3.12` 安裝。
- **鐵律**：跑任何 `browser-act` 指令前，Claude 一定先叫起這個 skill，再跑
  `browser-act get-skills core`（載入工作流程與安全規範），不可略過。

---

## 一次性前置：打開網路白名單

Claude Code on the web 的雲端沙盒**預設把出口網路鎖在可信任清單**（`Trusted` 等級），
一般網站（含 `github.com` 網頁、`browseract.com`）**預設是擋住的**。這是安全設計：
縮小資料外洩與惡意相依套件的爆炸半徑。

要用 BrowserAct，得把要連的網域開白名單：

1. **claude.ai/code**（瀏覽器版，不是桌面 App）→ 點顯示環境名稱的**雲朵圖示** → 開環境選擇器。
2. 滑到你的環境那列 → 右側**齒輪圖示** → 開設定對話框。
3. **Network access** → 選 **Custom** → 在 **Allowed domains** 填：

   ```
   github.com
   *.github.com
   *.githubusercontent.com
   github.githubassets.com
   browseract.com
   *.browseract.com
   ```

4. 勾 **「Also include default list of common package managers」**（保留 npm/PyPI 等預設放行）。
5. 存檔。

> **重要**：網路政策在 **session 啟動當下**套用。改完後**現有 session 不會生效**，
> 要**開一個新的 cloud session** 才吃到新設定。

### 為什麼用 Custom 而不是 Full
- **最小權限**：只開你要用的網域，其他全世界照舊擋著，幾乎不犧牲安全。
- **Full** 會放行任何網站，方便但把那層保護整個拿掉。

---

## 免每次重裝：Setup script

容器是臨時的（回收就沒了），CLI 每個新 session 要重裝。把
`scripts/setup_browser_act.sh` 的內容貼進**環境設定的 Setup script 欄位**，
之後每個新 session 一開就自動裝好 `browser-act`。技能檔本身已進版控，會自動載入，
不需要腳本處理。

---

## 怎麼用（日常）

用白話交代任務即可，Claude 會自動觸發 skill，例如：

- 「幫我開 X 網站、登入、把 Y 資料抓下來」
- 「監看這個頁面，有變動通知我」
- 「這個表單幫我填一填、送出前先給我看」
- 「這幾個網址平行抓內容」

### 內建保護（會遇到的）
- **敏感動作前一定先問**：建立瀏覽器、登入、送出表單、上傳檔案等，會停下等你明確同意。
- **不碰密碼**：需要你登入的網站，用 `remote-assist`（給你連結、你自己操作）或匯入 cookie，
  不要求你把密碼交出來。所有登入狀態**只留本地**。
- **API key 一次性**：`browser-act auth login` 註冊一次後，stealth 功能持續可用。

### 常見指令路徑
- **快速抓內容**（免開 session、可平行）：`stealth-extract <url>`
- **完整互動**（要登入/點擊）：`browser open` → `state` → `click`/`input` → `wait stable` → 抽資料 → `session close`
- **多帳號/多頁平行**：同一瀏覽器開多個 session（共用登入），或建多個瀏覽器（隔離 cookie）

---

## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `API key required` | 還沒註冊：`browser-act auth login` 拿連結完成註冊 → `browser-act auth poll` |
| `auth login` 回 403 | `api.browseract.com` 沒開白名單（見上方 Custom 設定） |
| 開網頁回 `chrome-error` / `ERR_CERT_AUTHORITY_INVALID` | 走官方 stealth 路徑即可，不要用本地 chrome hack |
| `get-skills core` 沒載到 | 一定要先叫 skill、再跑指令，不能直接 Bash |
