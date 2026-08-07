# 20 — Claude 工具棧全盤點（外掛 / 技能 / MCP）

> 來源：Ivan 提供的 IG 圖文清單（8 外掛 + 8 技能 + 8 MCP + 最優先 3 項）。
> 盤點與補全日期：2026-08-04。
> 圖例：✅ 已裝好可用 ｜ 🟡 已設定、差一步授權/金鑰 ｜ 🟢 已註冊目錄、一行指令可裝 ｜ 📋 需在個人機手動裝

---

## 最優先 3 項（圖 7/7）

| 項目 | 狀態 |
|---|---|
| marketingskills（外掛） | ✅ 已啟用 `marketing-skills@marketingskills`（49 個行銷技能） |
| frontend-design（技能） | ✅ 已裝進 `.claude/skills/frontend-design/`（Anthropic 官方版，含 Apache-2.0 授權檔） |
| notion（MCP） | 🟡 claude.ai connector **已安裝但未授權** — Ivan 去 claude.ai → 設定 → Connectors → Notion → 按 Connect 完成 OAuth 即通。`.mcp.json` 也已加（本機 Claude Code 用 `/mcp` 授權） |

---

## MCP 伺服器（8）

| # | 項目 | 狀態 | 說明 |
|---|---|---|---|
| 1 | notion | 🟡 | 見上。官方端點 `https://mcp.notion.com/mcp` 已進 `.mcp.json` |
| 2 | slack | ✅ | claude.ai connector 已連線可用 |
| 3 | granola | 🟡 | `.mcp.json` 已加 `https://mcp.granola.ai/mcp`。首次使用跑 OAuth，需 Granola 帳號（會議筆記 app，Mac/iOS） |
| 4 | zapier | 🟡 | `.mcp.json` 已加。去 [mcp.zapier.com](https://mcp.zapier.com) 建自己的 MCP server 後 `export ZAPIER_MCP_URL=<你的專屬URL>` 蓋掉預設值 |
| 5 | perplexity | 🟡 | `.mcp.json` 已加官方 `https://api.perplexity.ai/mcp`。需 `export PERPLEXITY_API_KEY=...`（付費 API）。**內建 WebSearch 已覆蓋大部分需求，可不急** |
| 6 | context7 | ✅ | `.mcp.json` 已加 `https://mcp.context7.com/mcp`，**免金鑰直接能用**（要更高限額再去 context7.com/dashboard 拿免費 key） |
| 7 | higgsfield | 🟡 | `.mcp.json` 已加 `https://mcp.higgsfield.ai/mcp`。首次使用 OAuth，需 Higgsfield 帳號。若連不上，去 [higgsfield.ai/mcp](https://higgsfield.ai/mcp) 複製正式 URL 後 `export HIGGSFIELD_MCP_URL=<URL>` |
| 8 | agent-browser | ✅ | **已由 `browser-act` skill 覆蓋**（同類瀏覽器自動化 CLI，功能等價：開網站、登入、抓資料、填表、截圖、過驗證碼）。原版是 vercel-labs/agent-browser |

**補充**：META connector 也在待授權狀態，順手一起授權。

---

## 外掛（8）

| # | 項目 | 狀態 | 說明 |
|---|---|---|---|
| 1 | marketingskills | ✅ 已啟用 | `coreyhaines31/marketingskills` — CRO、文案、SEO、廣告、成長，49 技能 |
| 2 | social-media-skills | ✅ 已啟用 | `social-media-skills/skills` — 貼文、Threads、輪播、圖說，106 技能（餐飲行銷直接用得上） |
| 3 | gstack | 📋 | Garry Tan 的 23 工具組，**不是標準 plugin**，Mac 上 `git clone https://github.com/garrytan/gstack` 照 README 裝 |
| 4 | superpowers | 🟢 已註冊 | 會強制改變開發流程（先 brainstorm/plan 才動工），怕跟本 repo 既有流程打架所以沒預設開。要用：`/plugin install superpowers@superpowers-marketplace` |
| 5 | codex (codex-plugin-cc) | 🟢 已註冊 | OpenAI 官方 `openai/codex-plugin-cc`。需要 ChatGPT 帳號 + 裝 codex CLI 才有意義。要用：`/plugin install codex@openai-codex` |
| 6 | financial-services | ✅ 等價已有 | claude.ai 已啟用 financial-analysis、equity-research、earnings-reviewer、market-researcher、sp-global |
| 7 | claude-for-legal | 🟢 已註冊 | Anthropic 官方 12 個法務 plugin（美國法域為主）。要用：`/plugin install commercial-legal@claude-for-legal` 等 |
| 8 | claude-skills | 🟢 已註冊 | `alirezarezvani/claude-skills` 巨型合集（79 plugins / 348 技能包）。按需挑裝：`/plugin install <名稱>@claude-code-skills` |

---

## 技能（8）

| # | 項目 | 狀態 | 說明 |
|---|---|---|---|
| 1 | frontend-design | ✅ | 官方版已 vendor 進 `.claude/skills/frontend-design/` |
| 2 | humanizer | ✅ 已啟用 | `blader/humanizer` plugin — 消除 AI 味文字 |
| 3 | ai-second-brain | 🟡 部分覆蓋 | 本 repo 的 `digest` skill + `docs/knowledge/` 帝國大腦已做同一件事。完整版（接 Gmail/NotebookLM/iMessage）是 Mac 個人機技能：`git clone https://github.com/charlie947/ai-second-brain ~/.claude/skills/ai-second-brain` |
| 4 | notebooklm-skill | 📋 | 需 Google 登入 + 本機瀏覽器自動化，裝在 Mac：`git clone https://github.com/PleasePrompto/notebooklm-skill ~/.claude/skills/notebooklm` |
| 5 | claude-seo | 🟢 已註冊 | `AgricIDaniel/claude-seo`（25 子技能 + 18 agents）。要用：`/plugin install claude-seo@agricidaniel-claude-seo` |
| 6 | hyperframes | 🟢 已註冊 | HeyGen 官方 HTML→影片渲染。要用：`/plugin install core-skills@hyperframes`（渲染需 Node.js） |
| 7 | doc skills | ✅ | claude.ai 已有 docx / pdf / pptx / xlsx 四件套 |
| 8 | caveman | ✅ | 自製版已放 `.claude/skills/caveman/`（省 token 極簡回答模式） |

---

## Ivan 要親手做的清單（OAuth / 金鑰我無法代辦）

1. **Notion 授權（最優先）**：claude.ai → Settings → Connectors → Notion → Connect。
2. **META connector 授權**：同一頁順手完成。
3. **Zapier**：到 [mcp.zapier.com](https://mcp.zapier.com) 建 MCP server、勾選允許的 app 動作，把專屬 URL `export ZAPIER_MCP_URL=...`。
4. **Perplexity（可緩）**：有需要再買 API key，`export PERPLEXITY_API_KEY=...`。
5. **granola / higgsfield**：本機 Claude Code 跑 `/mcp` 逐一完成 OAuth（需先有各自帳號）。
6. **Mac 個人機安裝**：gstack、ai-second-brain、notebooklm-skill、codex CLI（見上表指令）。

---

## Context 成本與維護

- 已預設啟用的 3 個外掛（marketing-skills 49 + social-media-skills 106 + humanizer 1）會把技能描述載入每次 session。覺得肥：`/plugin disable social-media-skills@social-media-skills`。
- `/plugin` → Discover 分頁可看每個 plugin 的 **Context cost** 估算。
- 「已註冊」的 marketplace 只是登記目錄，**不佔 context**，裝了才算。
- `.mcp.json` 的 `${VAR:-預設}` 讀的是 **shell 環境變數**，不是 `.env` 檔（那是給 FastAPI app 用的）。
- 雲端 session 用 MCP 需要環境的網路白名單放行對應網域（`mcp.context7.com`、`mcp.notion.com` 等）。

---

## 這次補全動了哪些檔

| 檔案 | 動作 |
|---|---|
| `.mcp.json` | 新增 — 6 個 MCP server（notion/granola/zapier/perplexity/context7/higgsfield） |
| `.claude/settings.json` | 加 9 個 plugin marketplace 註冊 + 預設啟用 3 個 plugin + `enableAllProjectMcpServers` |
| `.claude/skills/frontend-design/` | 新增 — 官方 SKILL.md + LICENSE.txt |
| `.claude/skills/caveman/` | 新增 — 自製 |
| `docs/20_claude_stack.md` | 本文件 |
| `CLAUDE.md` | 對照表加一列指向本文件 |
