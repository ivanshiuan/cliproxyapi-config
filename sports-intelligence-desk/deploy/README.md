# Path C — 完整上線部署

> 上線前 gate：先在專案根跑 `node qa.js`，必須 **🟢 全綠（1380 斷言 0 失敗）** 才部署。

## 架構

```
手機/瀏覽器  →  靜態 App (Pages/Netlify)  →  Cloudflare Worker proxy  →  賽事 API
                                              (藏 key、解 CORS、host 白名單)
```

## 步驟

### 1. 部署 Worker proxy（解 CORS + 藏 key）
```bash
cd deploy
npm i -g wrangler
wrangler login
wrangler secret put APIFOOTBALL_KEY      # 貼上 api-football.com 的 key
# 若用 football-data：wrangler secret put FOOTBALLDATA_KEY
wrangler deploy
# → 得到 https://sid-live-proxy.<你>.workers.dev
```

### 2. 部署 App 本體（純靜態）
擇一：
- **Cloudflare Pages**：連 GitHub repo，build 指令留空、輸出目錄設 `sports-intelligence-desk`
- **Netlify**：拖整個 `sports-intelligence-desk/` 資料夾
- **GitHub Pages**：把資料夾推上去、開 Pages

### 3. App 設定（手機上，免 console）
打開 App → **⚙️ 設定** 分頁：
- Provider：`API-Football`
- API Key：留空（key 在 Worker）
- Proxy：`https://sid-live-proxy.<你>.workers.dev/?u=`
- 按 **🔄 測試連線並更新** → 成功會顯示抓到幾場、自動重算

### 4. 安裝成手機 App
瀏覽器開 App → 分享 → 加到主畫面（PWA，可離線）

## 上線檢查清單

- [ ] `node qa.js` 🟢 全綠
- [ ] Worker 部署成功、secret 已設
- [ ] App 設定頁 🔄 顯示「成功」
- [ ] 主控台四場標的正常、台灣時間正確
- [ ] 賽後在 SID.results（或 live 自動）有比分 → 時間軸頁回測出分
- [ ] 手機已加到主畫面

## 安全

- key 只存 Worker secret，前端永遠看不到
- Worker 有 host 白名單，不會變成開放代理
- 純靜態 App 無後端、無使用者資料外洩面

## 維運

- 賽前依時間軸 T-72h→T-90m 重新整理（按 🔄）
- 賽後回測累積 → 校準模型（見 docs/PM_MASTER_PLAN.md 第 9 節）
