---
name: deploy-sid
description: 部署 Sports Intelligence Desk 到 Cloudflare Pages。當使用者說「部署 SID / 上線 / deploy / 把網站推上去 / 更新線上版」時呼叫。會自動 build、預檢 token 與網路 egress、執行 wrangler pages deploy，並印出 498-win.pages.dev 網址；卡關時給出精確的環境設定指引。
---

# Deploy SID｜一鍵部署到 Cloudflare Pages

把 `sports-intelligence-desk/` 的最新版部署到 Cloudflare Pages 專案 **`498-win`**
（網址 `https://498-win.pages.dev`）。整條流程已封裝在 `sports-intelligence-desk/deploy.sh`。

## 執行步驟

直接跑（不要手動拆步驟，腳本自己會預檢）：

```bash
cd sports-intelligence-desk && make deploy PROJECT=498-win
```

腳本依序做：① 檢查 `CLOUDFLARE_API_TOKEN` → ② 預檢能否連到 `api.cloudflare.com`
→ ③ `node build.js` → ④ `wrangler pages deploy dist/site` → ⑤ 印出網址。

## 兩個前置條件（都在 web 環境設定裡，session 啟動才生效）

1. **Environment variables**：`CLOUDFLARE_API_TOKEN`（權限含 Account · Cloudflare Pages · Edit）。
   選填 `CLOUDFLARE_ACCOUNT_ID`（32 位 hex；token 綁單一帳號時 wrangler 可自動推斷）。
2. **Network access = Custom**，Allowed domains 需含 `api.cloudflare.com` 與 `*.cloudflare.com`。
   （預設 Trusted 不含 Cloudflare API，會被 egress 擋下。）

## 卡關時怎麼回報

腳本會自己分辨並印出原因，照它說的做即可：

- `缺少 CLOUDFLARE_API_TOKEN` → 去環境設定加環境變數，**開新 session** 再跑。
- `egress 防火牆擋掉 api.cloudflare.com` → 環境設定 → Network access → Custom →
  加 `api.cloudflare.com`、`*.cloudflare.com`，存檔後**開新 session**。
- `token 被 Cloudflare 拒絕 / 無效` → 重簽一把含 Pages:Edit 的 token。

**鐵律**：網路允許清單與環境變數都只在 **session 啟動時注入**。若使用者剛改完設定，
務必請他**開一個新的 session** 再執行部署，當前 session 吃不到新設定。

## 部署後

回報線上網址 `https://498-win.pages.dev`，並提醒：若 token 曾貼在對話紀錄裡，
建議到 Cloudflare 撤銷重簽一把、填回環境變數，保持零殘留。
