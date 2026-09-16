# 19 — 帝國第二大腦：架構與路線圖

> 目標：打造一條「**看到好內容 → AI 消化 → 歸檔成可複用知識 → 越養越聰明**」的流水線，
> 並把「裝了一堆 skill 卻沒在用、AI 不知道何時調用」這個痛點根治。

---

## 一、要解決的三個問題

1. **爬取 + 學習**：常看小紅書/YT/抖音/FB/TikTok 的有用內容，想讓 AI 幫忙整理、學起來、應用到帝國營運。
2. **知識庫**：目前只用 Notion，想要更強、AI 能原生操作、能長成「格局圖」的第二大腦。
3. **skill 調度**：裝很多 skill 但沒用到，也不確定 AI 會不會在對的時機調用。

---

## 二、工具選型（結論先講）

| 需求 | 選什麼 | 為什麼 |
|---|---|---|
| AI 的第二大腦 / 知識庫 | **git repo 的 markdown（本專案 `docs/knowledge/`）** 起步，之後升級 **Obsidian vault** | Claude Code 能**原生讀寫檔案**，不用 API；有版控 |
| 給人看的漂亮成品層 | **Notion**（保留） | UI 好，適合對外/對人 |
| 「九宮格 / 世界格局圖」 | **Obsidian**（Canvas + Graph view） | markdown 不用改格式，原生吃雙向連結 |
| 爬取需登入/反爬/社群平台 | **BrowserAct** | 會 render JS、能登入、過驗證、抽資料 |
| 公開網頁/YouTube | **WebFetch**（內建） | 免開瀏覽器、免白名單，最省 |

> **Notion vs Obsidian 一句話**：Notion 是「關在別人伺服器、AI 要用 API 一格格戳」；
> Obsidian 是「本機 markdown、AI 直接讀寫」。要當 AI 的工作記憶，Obsidian（或就用本 repo）勝。

---

## 三、知識流水線

```
看到好內容
  → digest 技能：WebFetch / BrowserAct 抓原文
  → AI 提煉成「知識卡」(docs/knowledge/_TEMPLATE.md 格式)
  → 歸檔 docs/knowledge/YYYY-MM-DD-<slug>.md
  → (可選) 放進 Obsidian vault → Graph/Canvas 長出格局圖
  → 之後做決策時，AI 依 applies_to 標籤自動撈相關卡片來用
```

- 知識卡價值在 **`applies_to` + 「So what」**，不是摘要。
- 已建：`docs/knowledge/README.md`、`_TEMPLATE.md`、`.claude/skills/digest/`。

### BrowserAct 爬社群平台的現實（重要）
| 平台 | 可行度 |
|---|---|
| YouTube / 公開文章 | ✅ 順（WebFetch 就夠） |
| Facebook 公開貼文 | ⚠️ 要登入、易觸風控 |
| 小紅書 / 抖音 / TikTok | ⚠️ 強登入牆 + 強反爬 + ToS 風險，大規模爬會封號 |

**正確玩法：單點消化你指定的內容**（丟連結/截圖 → 消化歸檔），**不做無腦全站爬**。

---

## 四、skill 調度：為什麼沒被用到 + 怎麼修

**運作原理（無魔法）**：AI 看得到每個 skill 的 **name + description**，你講的話**踩中哪個 description 的觸發詞就調哪個**。沒踩中＝那個 skill 等於不存在。

**沒被用到的三大原因**：
1. description 太籠統，沒列「使用者說 X/Y/Z 就用我」。
2. skill 太多變雜訊、命名重疊。
3. 有些根本沒授權（MCP connector 需先登入）。

**解法（本專案已有骨架）**：
- `CLAUDE.md` 的「**直接交付指令對照表**」= 人工版 skill 路由器。每個 session 都會讀 → **把「你會怎麼開口 → 調哪個」寫進去，AI 就照調**。
- 每個 skill 的 description 要**列滿觸發場景**（用你平常的講法當觸發詞）。
- 不用的**砍掉**，別留著當雜訊。

**目前實際擁有（已盤點，不多也不亂）**：
- skills：`browser-act`、`digest`、`deploy-sid`、`match-intel`
- commands：`check`、`handoff`、`morning`、`spec`、`swarm`
- agents：`restaurant-domain-expert`、`router-implementer`、`spec-writer`、`tiger-pm`

---

## 五、路線圖（哪些能做、哪些卡住）

| 階段 | 內容 | 狀態 |
|---|---|---|
| ✅ 已完成 | 知識庫結構 + 知識卡模板 + `digest` 技能 + skill 路由表更新 | 本 PR |
| ✅ 現在可用 | 用 `digest` 消化**公開連結**（WebFetch）→ 歸檔 | 立即可跑 |
| ⏳ 待開白名單 | 用 BrowserAct 抓**需登入/社群平台**內容、競品菜單監看 | 需先設 Network access = Custom + 開新 session（見 docs/18） |
| 🔜 之後 | 匯出到 Obsidian vault → 格局圖；競品定期監看排程 | 白名單通了再接 |

---

## 六、下一步建議

1. **先跑通 `digest`**：丟一條你最近看到覺得有用的公開連結，我當場消化＋歸檔，你就有第一張知識卡＋可複用模板。
2. **開網路白名單**（docs/18）→ 解鎖社群平台爬取與競品監看。
3. **累積到一定量**再評估要不要搬進 Obsidian 做格局圖。
