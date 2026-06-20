# 部署到「你自己的」Cloudflare Pages — 全新獨立、與 buffhotpot 零關係

> 目標：開一個**全新的 Cloudflare Pages 專案**，拿到一個**新網址**
> （`https://<你的專案名>.pages.dev`），跟既有的 buffhotpot 完全分開
> —— 不同專案 = 不同網址 = 不同設定 = 不共用任何東西。

---

## 為什麼這樣就一定「跟 buffhotpot 無關」

Cloudflare Pages 每個「專案(Project)」彼此完全隔離：
- 各自獨立的 `*.pages.dev` 子網域
- 各自獨立的部署、環境變數、自訂網域
- 你只要**新建一個專案**、取一個跟 buffhotpot 不一樣的名字，就天生隔離。
- **不要**選到 buffhotpot 那個專案、不要 upload 到它的目錄即可。

建議專案名（擇一或自取，決定你的網址）：`sid-terminal`、`match-intel`、`sid-desk`

---

## 要上傳的東西

只上傳這個乾淨資料夾（已剔除測試碼/文件，避免外洩）：

```
sports-intelligence-desk/dist/site/
├── index.html
├── manifest.webmanifest
├── sw.js
├── css/terminal.css
└── js/{data,history,engine,backtest,live,app}.js
```

先產生它：`cd sports-intelligence-desk && node build.js`（或 `make build`）

---

## 方法 A（最簡單，零 CLI，推薦）：Dashboard 拖拉上傳

1. 登入 Cloudflare → 左側 **Workers & Pages**
2. **Create** → 分頁選 **Pages** → **Upload assets**（直接上傳，不連 Git）
3. 專案名稱填 **你取的新名字**（例：`sid-terminal`）← 這就是你的網址
4. 把 `dist/site/` 裡的**檔案/資料夾整包拖進去** → **Deploy**
5. 完成 → 網址 `https://sid-terminal.pages.dev`

> 之後要更新：同專案 → **Create new deployment** → 再拖一次新的 `dist/site/`。

## 方法 B（CLI，一行）：wrangler

在你自己的電腦（有 Node）：
```bash
cd sports-intelligence-desk
npx wrangler login                 # 開瀏覽器登入你的 Cloudflare
node build.js                      # 產生 dist/site
npx wrangler pages deploy dist/site --project-name sid-terminal
```
或用 Makefile：`make deploy-cf PROJECT=sid-terminal`

## 方法 C（自動）：連 Git 倉庫

Dashboard → Pages → **Connect to Git** → 選本 repo →
- Production branch：`claude/sports-intelligence-desk-dayved`（或你 merge 後的 main）
- Build command：`cd sports-intelligence-desk && node build.js`
- Build output directory：`sports-intelligence-desk/dist/site`
- 專案名取新的（非 buffhotpot）→ 之後 push 自動部署。

---

## 選用：CORS 代理 Worker（只有要在 App 內接即時賽事 API 才需要）

App 本體（上面）是純靜態，**不需要** Worker 就能跑（離線快照）。
若日後要 Live 抓賽程/比分/傷病，再單獨部署 `deploy/worker.js`（也取**新名字**，與 buffhotpot 無關）：
```bash
cd sports-intelligence-desk/deploy
npx wrangler deploy                       # 取新 worker 名，例：sid-proxy
npx wrangler secret put APIFOOTBALL_KEY   # 貼你的 key（不要寫進程式碼）
```
然後 App 設定頁 proxy 填 `https://sid-proxy.<你帳號>.workers.dev/?u=`。

---

## 我（Claude）為什麼不能直接幫你部署

這個雲端沙盒沒有 `wrangler`、沒有你的 Cloudflare token，且網路 egress 擋外連；
Cloudflare MCP 只能讀 Worker / 建 D1·KV·R2，**沒有 Pages 部署能力**。
所以最後「登入你帳號並部署」這一步必須由你執行 —— 上面三個方法挑一個即可，最快 2 分鐘。
