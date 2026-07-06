# 融合佈局計畫：以 OpenSquilla 為內核，長出自己的一套

> 目標：不是三套硬合併，而是「一個內核 + 兩個器官移植 + 一條生產線 + 既有通路」。
> 授權基礎：OpenSquilla Apache-2.0、OpenClaw / Hermes MIT——fork、改造、融合、商用皆合法，
> 只需保留原授權聲明、Apache-2.0 部分標注修改。
> 日期：2026-07-05

---

## 1. 融合姿勢：一個內核，兩個器官移植

三套硬合併不可行（OpenClaw 是 TypeScript，另兩套 Python）。正確策略：

```
你的新套件 = OpenSquilla 內核（Python, fork）
           + Hermes 的 learning loop（移植，同 Python 最順）
           + OpenClaw 的生態（走相容層接入，不搬程式碼）
```

| 來源 | 拿什麼 | 怎麼拿 |
|---|---|---|
| **OpenSquilla** | 微核心：SquillaRouter 智能路由、三層沙盒、SQLite 記憶 | 直接 fork 當底座 |
| **Hermes** | learning loop：任務後反思 → 自動生成 skill → 使用中自我改良 → 記憶固化 | 概念 + 部分程式碼移植（MIT 允許），skills 遵循 agentskills.io 標準，可攜性高 |
| **OpenClaw** | 龐大 skills 生態、頻道接法、社群踩過的坑 | 寫 ClawHub skill 相容轉接器 + MCP 互通；**不直接裝市集 skills**（已查出 824+ 惡意） |

learning loop 是**任務後**離線流程，路由是**任務中**即時決策——兩者互補不衝突。Hermes 的「越用越聰明」跑在 OpenSquilla 的「每一步都最省錢 + 預設沙盒」上。

---

## 2. 帝國四層：核 → 器官 → 工廠 → 通路

帝國不是一個更強的 app，是別人得在你上面蓋房子的底座。對照本專案既有資產：

| 層 | 帝國角色 | 現成籌碼 |
|---|---|---|
| **核（Kernel）** | fork 的 OpenSquilla + Hermes learning loop | 已審計、已懂骨架 |
| **器官（Vertical）** | RestSwarm 餐飲 OS、運動投研引擎 | 已有 6500+ LOC 後端 + 已測引擎 |
| **工廠（Factory）** | DevSwarm 自動把 spec 變成 skill/router | **最被低估的武器** |
| **通路（Distribution）** | LINE、餐飲客戶、投研使用者 | 已有真實落地管道 |

**關鍵洞察：DevSwarm 是印鈔機。** 別人靠 1,200 個貢獻者手寫 skill；你可用 DevSwarm 自動量產、還自帶測試。這是「機器造機器」的複利——帝國規模不再受一個人的時間限制。

---

## 3. 三塊別人進不來的地形（護城河）

1. **繁中 / 台灣市場 + 在地法規**（食安溯源、勞基法工時、統一發票生命週期）。全球專案不會為台灣餐飲寫這些。
2. **安全審計後的「可信版」**。已親手在 OpenSquilla 挖出一個 CVE 等級 critical；OpenClaw 敘事已被安全事故打爛。「經審計、預設安全」是定位空缺，而你有證據講這故事。
3. **token 效率 × 垂直場景**。省 90% token 對一般玩家是 nice-to-have，對「一天跑幾百家店」的餐飲營運是生死線——同技術價值放大十倍。

---

## 4. 硬化 Backlog（融合的第一批工作）

| # | 項目 | 對應審計發現 | 難度 | 狀態 |
|---|---|---|---|---|
| **1** | **WS 握手加 Origin allowlist + nonce 真驗證** | F0 CSWSH→RCE | 中 | ✅ patch 已備（`patches/opensquilla-ws-origin-validation.patch`） |
| 2 | 收斂 CORS 預設（`["*"]+credentials` → loopback allowlist） | F0 附帶 | 低 | 待辦 |
| 3 | token 移出 query string / 關 access_log query 記錄 | F0 附帶 | 低 | 待辦 |
| 4 | 密鑰 at-rest 加密 / 接 OS keychain | 密鑰-中 | 高 | 待辦 |
| 5 | installer 下載 `SHA256SUMS` 並比對 wheel + 依賴 hash pin | 供應鏈-中 | 中 | 待辦 |
| 6 | `.env` 寫入改 mkstemp 原子化、chmod 失敗不吞 | migrate-中 | 低 | 待辦 |
| 7 | 移植 Hermes learning loop（窮人版：任務後反思 + skill 草稿） | 能力缺口 | 高 | 待辦 |
| 8 | 用自己使用紀錄重訓 SquillaRouter 分類器 | 路由貼合 | 中 | 待辦 |
| 9 | ClawHub skill 相容轉接器 + 白名單審核 | 生態接入 | 中 | 待辦 |

**改動全部收在 `plugins/` 或 patch 層**，上游 rebase 才不痛——讓 OpenSquilla 的貢獻者無償幫你維護底座。

---

## 5. 執行順序（務實排程）

1. **隔離環境試裝試用兩週**（`auth.mode=token`、Strict 沙盒、測試 key）。
2. **套用 backlog #1~#3**（WS/CORS/token，網路面追平其自身水準）。← patch 已備
3. **重訓路由分類器**（backlog #8，兩邊 cliproxyapi 都能用）。
4. **移植窮人版 learning loop**（backlog #7，主工程，先拿 70% 效果）。
5. **skills 生態轉接**（backlog #9）。
6. 選一個窄 beachhead（一家真實餐廳跑一週，省下的 token + 零安全事故做成案例）。

前三步約一~兩週；第四步才是主工程。

---

## 6. 帝國心法（一句話）

> 別人用人數擴張，你用機器擴張（DevSwarm）；
> 別人用功能競爭，你用地形（台灣/安全/垂直）壟斷；
> 別人做產品，你做別人得站上去的底座。

先贏一家店、一個案例，把流程變資產（例如把這次的「安全審計流程」包成可重複跑的 skill），再用資產去換下一場更大的仗——帝國是一層一層長出來的，不是一次規劃出來的。

---

## 7. 風險提醒（不能省）

1. **維護負擔真實**：fork 一個每週在動的 preview = 認養一個孩子。改動做成 plugin/hook，定期 rebase upstream。
2. **先審碼再餵鑰匙**：匿名團隊 + 會碰 API key 的軟體，首跑一定隔離 VM + 低額度測試 key，絕不接 production credentials（呼應 `CLAUDE.md` 鐵律）。
3. **別低估 Hermes loop 移植量**：完整版綁 Honcho 用戶建模，是幾週工程；先做窮人版。
4. **與既有資產的綜效**：SquillaRouter「分類→分級路由」和 cliproxyapi 同構，重訓分類器兩邊共用；DevSwarm 的 spec→code 可反向量產 skills。
