---
source_type: 其他        # GitHub open-source repo（已 clone 實測分析）
source_url: https://github.com/oomol-lab/open-connector
captured_at: 2026-07-12
tags: [integrations, LINE, OAuth, credential-boundary, MCP, agent-tooling, Composio]
applies_to: [系統與AI]
status: reviewed
---

# open-connector：Composio 的開源替代品 — AI agent 連 SaaS 的閘道（架構參考用，不導入）

## 一句話重點

一個 Apache 2.0 的 TypeScript 連接器閘道，讓 agent 透過統一的憑證邊界呼叫 1,070 個 SaaS，
**但我們 Phase 1 需要的台灣整合（LINE、金流、電子發票、外送）全部沒有** —
價值不在拿來用，在於**做 LINE 整合正式版時抄它的架構**。

## 核心重點（3–5 條）

- **是什麼**：oomol-lab 出品的開源 Composio 替代品（2026-07 v1.0.3、1.5k stars）。
  Docker + SQLite 自架，提供 HTTP `/v1/actions/*`、OpenAPI、**MCP endpoint（`/mcp`）**、TS SDK、CLI。
- **核心設計 = 憑證邊界**：OAuth token / API key 留在 runtime 內，agent 只拿到
  action 的 JSON Schema 契約和執行結果，永遠碰不到祕密；每次執行自動寫 redacted 稽核日誌。
- **關鍵缺口（實測 `ls src/providers` 逐一核對）**：1,070 providers 裡
  ❌ LINE（只有 Linear）❌ ECPay/藍新/LINE Pay/街口 ❌ 台灣電子發票
  ❌ foodpanda/Uber Eats ❌ QuickBooks/Xero。有的是美國生態（7shifts、Square、Stripe、Google 全家桶）。
- **品質評估**：抽查 Slack / 7shifts / ecologi 三個 provider，都是手寫等級的 typed 實作
  不是垃圾生成；但全 repo 只有 24 個測試檔對 1,070 providers，長尾覆蓋薄。
- **可擴充**：有完整 add-provider 工作流（`CONTRIBUTING.md` + `.codex/skills/add-provider/SKILL.md`），
  理論上可自建 LINE provider 貢獻上去 — 但等於把關鍵路徑建在別人的 TS codebase 上，不划算。

## 可用在帝國哪個環節（So what）

**系統與AI — `restaurant_api/integrations/` 的架構藍圖。** 三個具體用法：

1. **做 LINE 整合正式版時（docs/09 範圍）**，對照它抄三件事：
   - 憑證與業務邏輯的**邊界隔離**（憑證只活在一層，handler 拿 context 不拿 secret）
   - **每個 action 一份 JSON Schema 契約**（input/output 都有 schema，agent 可自省）
   - 執行紀錄 redact 後進稽核 — 跟我們 `audit_service` 理念同路，可直接銜接
   - 關鍵檔案：`src/core/execution.ts`、`src/core/action-policy.ts`、
     provider 四件套模式 `actions.ts / definition.ts / executors.ts / runtime.ts`
2. **未來要接台灣以外的長尾 SaaS**（行銷自動化、國際工具）又不想付 Composio：
   自架這套 + MCP endpoint 直接掛給 Claude，是現成方案。
3. **DevSwarm 若進化到需要 agent 操作外部服務**：它的 action catalog + 憑證邊界
   就是「不把 API key 塞進 prompt」的正確做法範本。

**明確不做**：不導入為依賴（多養一個 Node 22 服務 + 租戶憑證託付給 v1.0 年輕專案，
違反 docs/08 個資合規的保守原則）。

## 行動項（Next action）

- [ ] 做 LINE 整合正式版（取代現有 Stub）時，回來讀這張卡 + 對照上面列的關鍵檔案設計 integrations 層
- [ ] 若哪天評估 Composio / 付費 iPaaS，先回來看這張卡（自架替代品已存在）

## 原文摘錄 / 逐字稿重點

> "Provider secrets stay behind the runtime boundary; agents receive metadata,
> safe account labels, and execution results needed for the run." — README

> 快速啟動：`docker compose up` → `http://localhost:3000`（含 Web Console）；
> 免驗證測試：`POST /v1/actions/hackernews.get_top_stories`

> 憑證設定範例：`PUT /api/connections/github` + `{"authType":"api_key","values":{...}}`；
> OAuth2 flow / token refresh / 加密另見 `docs/credentials.md`

實測數據（2026-07-12 shallow clone，56MB）：providers 1,070 個、actions 約 1.1 萬、
測試檔 24 個；部署選項 Docker / Fly.io / Cloudflare Workers（D1+R2）/ OOMOL SaaS。
