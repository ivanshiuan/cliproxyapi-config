# OpenSquilla 深度分析 + 三方比較

> 對象：[opensquilla/opensquilla](https://github.com/opensquilla/opensquilla) `main`（commit `7f72a32`）
> 比較對象：OpenClaw（Peter Steinberger）、Hermes Agent（Nous Research）
> 日期：2026-07-05

---

## 1. 它是什麼

OpenSquilla 是 2026 年 5 月公開的開源 AI agent，口號「**同樣預算、更高智能密度**」。核心是一個跑在本地的智能模型路由器，把每個請求分派到「最便宜且能勝任」的模型。明顯受 OpenClaw 啟發，並內建 `opensquilla migrate` 可直接把 OpenClaw / Hermes 的記憶、persona、skills、MCP 與頻道設定搬過來。

### 核心架構（微核心）

- **SquillaRouter** — 靈魂元件。本地 LightGBM + ONNX 分類器，依請求長度、語言、是否含程式碼、關鍵詞、語義嵌入評分，把請求動態分到 T0（最輕）~ T3（頂級）四級模型。打招呼/簡單摘要走便宜模型，複雜推理/寫程式才走頂級。
- **20+ LLM 供應商**：OpenRouter、OpenAI、Anthropic、Ollama、DeepSeek、Gemini 等，pluggable provider 層。
- **本地持久記憶**：SQLite 全文搜尋 + sqlite-vec 語義召回。
- **三層沙盒**：Standard / Strict / Locked；Linux 用 Bubblewrap、macOS 用 Seatbelt、Windows 原生後端。
- **15 個內建 skills**、MCP 客戶端 + 伺服器雙向、Slack/Discord/Telegram/飛書/Matrix 頻道、Electron 桌面 + Vue Web 控制台。

### 技術棧與體量

| 項目 | 值 |
|---|---|
| 主要語言 | Python 80%、TypeScript/JS/Vue 其餘 |
| 原始碼規模 | 238k LOC / 775 Python 檔 |
| Gateway | Starlette ASGI，預設 `127.0.0.1:18791` |
| License | Apache-2.0 |
| 最新版 | 0.5.0 Preview 1（2026-07-03） |

### 成本效率主張

第三方社群評測（PinchBench 1.2.1）指出：OpenClaw 得分 0.9255、OpenSquilla 0.9251（統計上等同），但完成全部任務 OpenClaw 花 \$6.23、OpenSquilla 只花 \$0.69 —— 約 **1/9 成本**。早期使用者回報 60~80% token 節省。

> ⚠️ **這些數字來自專案方與早期使用者，尚無獨立學術級驗證。** 引用時標明來源，勿當定論。

---

## 2. 成熟度與社群

| 指標 | 值 | 註 |
|---|---|---|
| Stars | 5.4k | 新專案 |
| Forks | 381 | |
| Commits | 787 | |
| Releases | 9 | 仍在 Preview |
| Open issues | 73 | 含多個 6 月 bug |
| 團隊 | **匿名 org** | 查不到具體維護者——對會拿你 API key 的軟體是信任扣分項 |

---

## 3. 三方比較

| 維度 | **OpenSquilla** | **OpenClaw** | **Hermes Agent** |
|---|---|---|---|
| Stars / Forks | 5.4k / 381 | **382k / 80k** | 209k / 38k |
| 發布 | 2026-05 | 2025 末爆紅 | 2026-02（Nous Research） |
| 版本狀態 | 0.5.0 **Preview** | 穩定，vYYYY.M.D 快車道 | v0.18.0 |
| 核心賣點 | **token 效率**（SquillaRouter） | 生態最大、頻道最全 | **自我進化**（learning loop，skills 越用越好） |
| 語言 | Python | TypeScript | Python |
| 內建 skills | 15 | ClawHub 市集數千個 | 97（28 類） |
| 沙盒/安全 | **預設三層沙盒**（fail-closed） | 歷史紀錄極差（多 CVE + 供應鏈事故） | 指令核准 + Docker 等 6 種隔離後端 |
| Token 成本 | 最低（~1/9） | **最高**（出名的 token 大戶） | 中等 |
| 團隊透明度 | ❌ 匿名 | ✅ Peter Steinberger + 1,200+ 貢獻者 | ✅ Nous Research |
| License | Apache-2.0 | MIT | MIT |

### OpenClaw 的安全包袱（背景，影響定位）

2026 年初 OpenClaw 經歷多向量安全危機：RCE（CVE-2026-25253，CVSS 8.8）、ClawHub 市集 341 個惡意 skills（ClawHavoc，後增至 824+）、2.1 萬個公網暴露實例、專竊設定檔的 infostealer；Cisco / Microsoft 皆出文警告。**OpenSquilla 的「預設沙盒」正是打這個痛點——但它自己在網路面也留了一個同類洞，見 `01_security_audit.md`。**

### Hermes 的定位（不同賽道）

Hermes 賭的不是省錢，是「agent 越用越懂你」：每次任務後跑 learning loop、自動生成/改進 skills、Honcho 用戶建模。十週衝到 110k stars，已在 OpenRouter 日均 token 用量超車 OpenClaw。**這是 OpenSquilla 沒有、值得移植的能力。**

---

## 4. 分維度裁決：有比 OpenClaw / Hermes 強嗎？

| 維度 | 裁決 |
|---|---|
| **成本效率** | ✅ **目前最強**。SquillaRouter 是三者中唯一認真解「agent 太燒錢」的架構。 |
| **安全設計** | 🟡 紙面最強（預設沙盒 > OpenClaw 裸奔），但有一個預設 critical 未清 + 團隊匿名反向風險。 |
| **成熟度/生態/社群** | ❌ **明顯最弱**。Preview、15 skills、5.4k stars 對上兩個數量級大的對手。 |
| **自我進化** | ❌ Hermes 獨有，OpenSquilla 無對等物。 |

### 結論

**不是「全面更強」的替代品，而是「OpenClaw 的省錢精簡版 + 更好的沙盒」。** 在成本效率這一個維度領先，其餘落後。對「自架代理、控管 API 成本」的使用情境（例如本 repo 的 cliproxyapi 場景）理念高度同構，**值得 fork 當內核**——但要靠融合（補 Hermes 的進化迴路、吸 OpenClaw 的生態、修掉它的安全洞）才能變成真正更強的一套。詳見 `02_fusion_plan.md`。

---

## 附錄：主要來源

- [opensquilla/opensquilla — GitHub](https://github.com/opensquilla/opensquilla) · [官網](https://opensquilla.ai/)
- [OpenSourceForU 發布報導](https://www.opensourceforu.com/2026/05/opensquilla-launches-open-source-ai-runtime-with-ml-routing-and-secure-sandboxing/)
- PinchBench 評測（note.com，第三方，數字未經獨立驗證）
- [openclaw/openclaw](https://github.com/openclaw/openclaw) · [OpenClaw 安全危機（Conscia）](https://conscia.com/blog/the-openclaw-security-crisis/) · [ClawHub 惡意 skills（The Hacker News）](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html)
- [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) · [The New Stack 比較](https://thenewstack.io/persistent-ai-agents-compared/)
