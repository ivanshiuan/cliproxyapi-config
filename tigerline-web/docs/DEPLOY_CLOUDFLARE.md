# TIGER LINE PRIME — 部署到 Cloudflare Pages

> **推薦專案名**：`tigerline` → 你的網址 `https://tigerline.pages.dev`
> Cloudflare Pages 每個「專案」彼此獨立，跟 `498-win`（SID）完全不會撞。

---

## 為什麼這裡不需要 Fly.io / Docker / Python 主機

TIGER LINE PRIME 的決策引擎（classifier / corridor / harness / stake /
recommender / review）**已 100% 移植成 vanilla ES modules JavaScript**，
在瀏覽器裡跑。伺服器端零計算、零狀態。所以：

- Cloudflare Pages 只當 CDN 發靜態檔案，全球 edge < 50 ms
- 零冷啟、零 idle spin-down
- 免費、免綁卡
- 你自己的網域可以直接掛上去（Cloudflare Pages 自訂網域）

---

## 方法 A（最簡單，零 CLI）：Dashboard 拖拉上傳

1. 登入 Cloudflare → 左側 **Workers & Pages**
2. **Create** → **Pages** → **Upload assets**
3. 專案名稱填 **`tigerline`**（或你想要的任何名字）
4. 把 `tigerline-web/` 資料夾**整包**拖進去 → **Deploy**
5. 完成 → 網址 `https://tigerline.pages.dev`

之後要更新：同專案 → **Create new deployment** → 再拖一次新的 `tigerline-web/`。

## 方法 B（CLI，一行）：wrangler

```bash
cd tigerline-web
npx wrangler login
npx wrangler pages deploy . --project-name tigerline
```

或用 Makefile 目標：`make deploy-tigerline`（下面加）

## 方法 C（自動 Git 連動）

Dashboard → Pages → **Connect to Git** → 選 `ivanshiuan/cliproxyapi-config`：
- Production branch：`main`（或當前開發分支）
- Build command：**留空**（純靜態，不用 build）
- Build output directory：`tigerline-web`
- 專案名：`tigerline`

之後 `git push` 到指定分支就自動部署。

---

## 掛你的自訂網域

Cloudflare Pages 專案頁 → **Custom domains** → **Set up a custom domain**
→ 輸入 `tigerline.你的網域.com`（或任何子網域）→ Cloudflare 自動配 DNS 和 HTTPS。

---

## 驗證部署成功

打開網址，做這 3 件事：

1. 點「載入範例」下拉 → 選 `belgium_nz_two_goal` → 按「載入」
2. 按 **Analyze** — 應該顯示：
   - Scenario：`two_goal_landing`
   - Confidence badge：`0.90`
   - Harness badge：`upgrade`（綠色）
   - Main Bet：`Belgium -1.5 · AH · A+ · 1250`
3. 展開 **Post-match review** → 輸入 `5` / `1` → 按 Review — 應該顯示：
   - Scenario: ✓ correct
   - Corridor: ✗ missed
   - Main: `win`
   - Score: `80/100` 左右

如果三項都對 = 部署成功、跟 CLI 完全一致。

---

## 更新流程

改完程式 → `git commit && git push` →
- **方法 A** 用戶：重新拖檔上傳
- **方法 B** 用戶：`wrangler pages deploy`
- **方法 C** 用戶：Cloudflare 自動幫你部署

---

## 目前尚未搬到 web 的功能

這些功能仍只在 Python CLI（`tiger` 指令）跑，因為需要 SQLite 持久化：

| 功能 | CLI 指令 | 備註 |
|---|---|---|
| 多本盤口 snapshot 收集 | `tiger snapshot add` | 需要 DB |
| Line movement 分析 | `tiger movement analyze` | 需要 ≥2 個 snapshot |
| 多平台 consensus | `tiger consensus show` | 同上 |
| Precision score 8 channel | `tiger precision score` | 綜合 snapshot + consensus |
| CLV tracker | `tiger clv record` | append-only ledger |
| 週報 calibration | `tiger report calibration` | 需要歷史結果 |

如果之後想把這些也搬到雲上，需要接 Cloudflare D1（SQL）或 KV。那是 Sprint 9+。

現在的 web 版能做的：**單場 analyze + 賽後 review + 資金分配** — 就是你的
日常決策 90% 場景。

---

## 障礙排除

**打開網址一片空白** → F12 開 Console，看有沒有 `Failed to load module`。
最常見是路徑錯了：Cloudflare Pages 對根路徑敏感，`./js/app.js` 應該就對。

**Analyze 按下去沒反應** → Console 應該有錯誤。如果 form 值有空格或不是數字
會被 `parseMatch` 擋下並顯示在畫面上的紅框裡。

**Analyze 結果跟 CLI 不一樣** → 開 Issue，因為 JS 應該跟 Python 100% 對齊，
17 個 parity 測試在 `tigerline-web/tests/canonical.test.js` 保證這件事。
