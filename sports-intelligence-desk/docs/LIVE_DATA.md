# 如何撈取最新資料（Live Data）— 誠實說明三條路

## 現況（誠實）

1. **這個雲端沙盒的網路是白名單制**。實測：TheSportsDB 被擋（"Host not in allowlist"）、
   football-data.org / api-sports.io 回 403。→ **目前環境無法直接 curl 外部賽事 API**。
2. **瀏覽器有 CORS 限制**。多數賽事 API 不允許網頁直連，需經 proxy。
3. 對接層（`js/live.js`）**已寫好**：支援 API-Football / football-data.org / TheSportsDB，
   抓不到時自動降級用內建快照，不崩、不影響分析。

---

## 路 A（現在就能用，零設定）：我用 WebSearch 幫你更新

對話中我用內建搜尋抓最新（第1輪比分、盤口就是這樣查證的），再寫進 `data.js`。
**你只要說「更新某場最新狀況」，我就上網查 → 校準資料 → 重跑引擎。**
- 優點：今天就能用、誠實標 verified/estimated
- 缺點：非自動、要你開口

## 路 B（半自動）：把賽事 API 加進環境白名單

這個沙盒的錯誤訊息直說了：「Add this host to your network egress settings」。
到 **Claude Code on the web 的環境網路設定**，把以下 host 加入 egress 允許清單：
```
v3.football.api-sports.io      （API-Football，免費額度 100 req/日）
api.football-data.org          （football-data，免費 token）
www.thesportsdb.com            （免費 key=3）
```
加完後我就能在這裡 `curl` 抓 live 資料 → 自動更新 `data.js` → 重算。
文件：https://code.claude.com/docs/en/claude-code-on-the-web

## 路 C（全自動，正式上線）：部署 App + Worker proxy

讓 App 在你手機/伺服器自己更新，需解決 CORS：

1. **拿 API key**（擇一）
   - API-Football：https://www.api-football.com （免費 100 req/日，有賽程/比分/盤口/傷病/先發）
   - football-data.org：https://www.football-data.org （免費 token，賽程/比分）
2. **架 Cloudflare Worker 當 proxy**（解 CORS，順便藏 key），約 20 行：
   ```js
   export default {
     async fetch(req, env) {
       const target = new URL(req.url).searchParams.get("u");
       const r = await fetch(target, { headers: { "x-apisports-key": env.KEY } });
       const body = await r.text();
       return new Response(body, { headers: { "access-control-allow-origin": "*" } });
     }
   };
   ```
3. **App 設定**（瀏覽器 console 或之後做設定頁）：
   ```js
   SID.liveConfig = { provider: "apifootball", key: "", season: 2026,
                      proxy: "https://你的worker.workers.dev/?u=" };
   await SID.refresh();   // 抓最新 → 更新賽程/比分 → 重算
   ```
4. 部署 App 本體：Cloudflare Pages / Netlify / GitHub Pages（純靜態，拖上去即可）

---

## 對接層提供什麼

| 函式 | 作用 |
|---|---|
| `SID.liveConfig` | 設 provider / key / season / proxy |
| `SID.refresh()` | 抓 live → 更新賽程 + 賽後比分(→回測) + **傷病同步** → 標 `meta.live=true` |
| `SID.fetchFixtures()` | 包裝 refresh，回傳 matches/teams/meta/status |

> **傷病同步（API-Football）**：refresh 會抓 `/injuries`，**只比對 curated 名單裡已存在的球員**
> （依姓氏比對），把狀態改 out/doubt，回傳 `injuriesApplied` 筆數。**絕不無中生有新增球員、
> 不亂造傷病** —— 守住「未查證傷病不復活」鐵律。其餘 provider 暫不抓傷病。
>
> 球隊評級(Elo/xG/ppda…)仍用本系統 curated 先驗 —— 那是模型核心智慧，不是 API 給得了的。這是設計，不是缺陷。

---

## 我的建議

- **馬上**：用路 A（我 WebSearch 更新），賽事要開了、最快。
- **這兩天**：路 B（加白名單）讓我能在這環境自動抓。
- **要當產品**：路 C（Worker proxy + 部署），App 在手機自更新。
