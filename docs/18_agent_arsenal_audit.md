# 霸虎帝國軍備總盤點 — Agent / Skills / 自動化 稽核報告

> 日期：2026-07-05 ｜ 稽核分支：`claude/agent-setup-audit-3y44c9`
> 性質：全面盤點 + 文件作假稽查 + 九宮格戰略佈局
> 結論先講：**沒有發現惡意造假；但有 4 份文件嚴重過期、2 條自動化斷線、1 個「模擬 vs 實戰」必須認清的真相。**

---

## 1. 帝國現況：三大戰線

| 戰線 | 內容 | 規模 | 狀態 |
|---|---|---|---|
| **RestSwarm** 餐飲營運 OS | FastAPI + PG16 + 26 表 + LINE + 輪盤行銷 + 會員成長飛輪 | 10 套 migration、437+ 主線測試 | ✅ 主力，已過 5 道品質閘門 |
| **DevSwarm** AI 代工廠 | LangGraph **5-agent** 蜂群（PM→Architect→Coder→QA→**Reviewer**）+ Codex 法則單一真相源 + Hermes 通知 + prompt 版本登記 + Reviewer 回歸 eval | 2,500+ LOC | ⚠️ 可用但 **ANTHROPIC_API_KEY 未填**，目前熄火 |
| **TIGER LINE / SID** 體育投研 | `tigerline/`（V3.0 CLI：分類器/走廊/凱利/CLV/合規守衛，179 測試）+ `sports-intelligence-desk/`（8 子模型引擎、Monte-Carlo 回測、PWA）+ `tigerline-web/` | 3 個子專案 + Cloudflare/GitHub Pages 部署線 | ✅ 程式真實，但**尚無實戰數據**（見 §5） |

---

## 2. Agent 軍備（.claude/agents/ 4 名正規軍 + 平台軍團)

### 專案自訂 subagent

| Agent | 用途 | 工具權限 | 備註 |
|---|---|---|---|
| `restaurant-domain-expert` | 台灣餐飲法規/食安/發票/勞基法領域顧問 | 唯讀（Read/Glob/Grep） | 答案錨定 docs/04、docs/08 |
| `router-implementer` | 一個 router 完整切片（schema+service+router+test），4 檔專屬所有權、可 4 隻平行 | 讀寫+Bash | 平行開發不撞檔的設計 |
| `spec-writer` | DevSwarm 任務簡報產生器（AC≥10、In/Out-of-scope 強制） | 讀寫 | 以 profit_calc.md 為典範 |
| `tiger-pm` | TIGER LINE 流程助理：賽前輸入完整性檢查、賽後復盤、模式統計 | 唯讀+Bash，**鐵律：永不推薦下注** | 指定 sonnet 省成本 |

### 平台內建可徵調

`Explore`（大範圍搜索）、`Plan`（架構規劃）、`general-purpose`、`claude-code-guide`，以及 **Workflow 引擎**（可確定性編排數十隻 subagent：fan-out、對抗式驗證、judge panel — 目前完全沒用到，是最大閒置火力）。

---

## 3. Skills 軍火庫

### 專案 Skills（.claude/skills/）
- **`/deploy-sid`** — 一鍵部署 SID 到 Cloudflare Pages（498-win.pages.dev），含 token/egress 預檢
- **`/match-intel`** — 賽事投研分析（華爾街投研框架），鐵律：機率一律由 engine.js 算、禁止手寫

### 專案 Commands（.claude/commands/）
- **`/check`** — 五道品質閘門一鍵跑（ruff+pyright+pytest+alembic+db-smoke）
- **`/morning`** — 晨間喚醒儀式（起 DB、測試、backlog、blockers）
- **`/handoff`** — 重寫指揮官交接書（含「不得謊報完成」自我約束條款）
- **`/spec`** — 寫新 DevSwarm 任務簡報
- **`/swarm`** — 帶預算護欄（$5）跑蜂群任務 + promote 流程

### 平台級 Skills（隨時可用）
`/code-review`、`/security-review`、`/simplify`、`/verify`、`/deep-research`（多源對抗式查證研究）、`/loop`（循環任務）、`/dataviz`、`claude-api` 參考、`update-config` 等。

---

## 4. 自動化與防線

### Hooks（.claude/settings.json — 已上膛的牆）
| Hook | 作用 |
|---|---|
| PreToolUse (Edit/Write) | **硬擋改 `.env`**（exit 2 直接封鎖） |
| PreToolUse (Bash) | **硬擋 push main/master 與 force push** |
| PostToolUse (Edit/Write) | `.py` 檔自動 ruff format |
| SessionStart | 自動起 PostgreSQL + 檢查 API key（本次已回報 key 缺失） |
| PreCompact | 壓縮事件記錄到 compact-log |

另有 45+ 條精細 permissions allowlist（唯讀指令免問）＋ 10 條危險指令 ask 清單。

### GitHub Actions（雲端三迴圈 + CI + 部署）
| Workflow | 觸發 | 狀態 |
|---|---|---|
| `daily-brief.yml` | cron 週一–五 08:00 台北 | ✅ 活著（7/1、7/2、7/3 都有 commit 證據） |
| `pr-babysitter.yml` | PR 事件 | ✅ |
| `changelog-drafter.yml` | push 到 `claude/autonomous-resttech-enterprise-oW9jp` | ⚠️ **綁死舊分支**（見 §5-D） |
| `deploy-pages.yml` | push 到同一舊分支 | ⚠️ 同上 |
| `ci.yml` | main / PR→main | ✅ 但開發分支平時不跑，只在開 PR 時攔截 |
| `deploy-tigerline.yml` | main + 手動 | ✅ |

### 雲端 Routines（Claude 排程觸發器）
本次 session 的 trigger 工具權限串流異常、**無法讀取清單** — 有沒有殘留的雲端排程需要你在 claude.ai 介面確認一次。

### MCP 外部武器庫（連接器）
GitHub（全套 PR/issue/CI 操作）、Slack、Gmail、Google Calendar、Google Drive、Figma、Gamma、Adobe Creative、**META 廣告全家桶**（campaign/受眾/像素/AB test）、Windsor.ai（350+ 資料源含 GA4/Shopify/Stripe）。
⚠️ 其中 3 個 MCP server 需要重新 OAuth 授權才能用（本 session 非互動、無法代辦，請去 claude.ai 連接器設定授權）。

### 遠端分支墳場
GitHub 上目前有 **32 條 `claude/*` 分支**，多數已完成歷史使命（launch-wheel、tiger-v2、各研究分支）。建議定期清理已合併/已棄置分支，避免自動化誤綁舊分支（§5-D 就是這樣發生的）。

---

## 5. 作假稽查結果（你要的「揪出來」）

**判定：沒有惡意造假 — 數字都曾經是真的，但「過期未更新」讓文件在說謊。** 逐項：

### A. `CLAUDE.md`（專案大腦）— 🔴 過期最嚴重
| 宣稱 | 實際 | 落差 |
|---|---|---|
| 「106 個 pytest」 | 靜態掃出 **617 個測試函式**（主線 ~438 + tigerline 179） | 差 5.8 倍 |
| 「3 份遷移」 | **10 份** alembic migrations | |
| 「25 表」 | HANDOFF 自己已說 26 表 | |
| 「4-agent 蜂群」 | 已升級 **5-agent**（新增對抗式 Reviewer） | |
| 「docs/ 10 份戰略文件」 | **17 份**（12–17 全沒登記） | |
| 「永遠在 `claude/autonomous-...-oW9jp` 分支」 | 現行工作流是每任務一個 `claude/*` 分支 + PR | 規則已失效 |
| 完全沒提 | tigerline / tigerline-web / sports-intelligence-desk / tiger-pm agent / 2 個 skills / 三迴圈自動化 | **帝國一半的領土不在地圖上** |

### B. `MORNING_BRIEF.md` — 🔴 v1 化石
「47 個測試全綠、18 表」— 這是開國第一天的數字，現在會誤導任何讀它的人（包括未來的 Claude session）。

### C. `COMMANDER_HANDOFF.md` — 🟡 停在 2026-06-06
內容本身誠實（398 pytest 當時為真），但已一個月沒重寫，引用的 `claude/launch-wheel-game-campaign-t7octp` 分支任務早已合併。

### D. 自動化斷線（隱性作假：看起來有、實際不會跑）
`changelog-drafter.yml` 與 `deploy-pages.yml` 都只監聽舊的 autonomous 分支 — **現在的工作分支 push 不會觸發它們**。RELEASE_NOTES_DRAFT 的 7/4 更新是 loop 直接 commit 的，一旦舊分支停止活動，這兩條線就是殭屍。

### E. TIGER LINE / SID —「模擬 vs 實戰」必須認清
- `backtest_sim.js`（188 行）與 `qa.js`（214 行、66 個 pass/fail 檢查點）**是真實程式碼**，且回測檔頭自帶「誠實聲明」：DGP 與引擎公式刻意不同以防循環自證。「8000 場」是執行參數（`node backtest_sim.js 8000`），「14900/14900」= 149 檢查 × 100 輪，數學合理。**這部分不是造假。**
- **但**：`data/matches/` 只有一個 `.gitkeep` — **零場實戰紀錄**。所有校準/命中率數字全部來自合成模擬。tiger-pm 的「recent classifier patterns」目前無資料可總結。模擬校準 ≠ 真實 Alpha，程式碼自己也這麼寫。這是目前帝國文宣最容易自我膨脹的地方，先講清楚。

### F. 其他小項
- repo 名 `cliproxyapi-config` 與內容完全無關（歷史遺留，改名成本低收益高）。
- 雲端容器 `.venv` 不存在 → PostToolUse 的 ruff format hook 靜默失效、pytest 無法直接跑（雲端 session 需在 SessionStart 補 venv bootstrap）。
- `.env` 無 ANTHROPIC_API_KEY → DevSwarm 戰線整條熄火中。

---

## 6. 九宮格戰略佈局（曼陀羅式）

```
┌────────────────────┬────────────────────┬────────────────────┐
│ ① DevSwarm AI 代工廠 │ ② RestSwarm 餐飲 OS  │ ③ 行銷成長飛輪        │
│ 5-agent 蜂群+Codex   │ 26表+輪盤+會員飛輪    │ META Ads+Windsor+LINE│
│ 缺:API key、閒置     │ 缺:真實店家上線       │ 缺:MCP 授權、實投放   │
├────────────────────┼────────────────────┼────────────────────┤
│ ⑧ 治理與品質防線      │ ★ 霸虎帝國            │ ④ 體育投研 TIGER/SID  │
│ hooks+5閘門+CI       │ AI 作業系統           │ 引擎+回測+合規守衛     │
│ +對抗式Reviewer      │ (指揮官: Ivan)        │ 缺:實戰數據 0 場       │
├────────────────────┼────────────────────┼────────────────────┤
│ ⑦ 情報中樞           │ ⑥ 指揮通訊            │ ⑤ 內容設計兵工廠      │
│ daily-brief+deep-   │ Slack+Gmail+Calendar │ Figma+Adobe+Gamma    │
│ research+loop       │ +PR babysitter       │ +dataviz+Artifact    │
│ 缺:主動推送到手機     │ 缺:三個MCP待授權      │ 缺:尚未實際產出過      │
└────────────────────┴────────────────────┴────────────────────┘
```

### 每格的下一步（優先序由高到低）

1. **② RestSwarm**：找 1 家真實試點店（docs/06 的 D 決策）— 全帝國最值錢的一步，其他都是為這格服務。
2. **⑧ 治理**：本報告 §7 的修復清單（文件同步 + workflow 分支修正）。
3. **④ TIGER/SID**：停止加功能，改成「累積 30 場實戰 review 資料」— 讓 `data/matches/` 從 0 變成有東西，模擬數字才有對照組。
4. **① DevSwarm**：填 API key、跑完剩餘 6 個純函式 spec、讓 Reviewer eval 累積回歸基準。
5. **③ 行銷**：授權 Windsor/META MCP → 把輪盤活動成效接到真實廣告投放閉環（RestSwarm 的 stats API 已經有數據出口）。
6. **⑤ 內容**：用 Figma/Gamma MCP 把輪盤海報、店長手冊、投研 memo 模板化 — 一次設計、每店複用。
7. **⑦ 情報**：daily-brief 升級成 push 到 LINE/Slack（Hermes 已有骨架），指揮官睜眼就看到戰報，不用開 GitHub。
8. **⑥ 通訊**：授權 3 個 MCP server，把 PR babysitter 的通知從 GitHub comment 升級到 Slack DM。

### 多 AI 協同的三個升級方向（大腦思維模式升級）

1. **Workflow 編排引擎**（現有但零使用）：把「多 router 平行實作」「全 spec 掃蕩」「對抗式全庫稽核」改用確定性 workflow 跑 — 一次 fan-out 10+ subagent、每個發現經 3 lens 對抗驗證，比單線 session 快且不漏。
2. **雙塔制衡**：DevSwarm（API 蜂群）產碼 → Claude Code subagent（/code-review + /security-review）當獨立監察院 → 兩套不同 prompt 體系互相抓錯，Reviewer eval 記錄長期回歸。
3. **文件即戰情系統**：CLAUDE.md 瘦身成「不變法則」；動態數字（測試數/表數/docs 清單）全部改由 daily-brief 自動生成到 LOOP_STATE.md — **從根本消滅「文件過期＝說謊」這類問題**。

---

## 7. 修復清單（本次稽核後的行動項）

| # | 行動 | 負責 | 狀態 |
|---|---|---|---|
| 1 | CLAUDE.md 更新：617 測試、10 migrations、5-agent、17 docs、三戰線地圖、分支規則改為「每任務 claude/* + PR」 | Claude | 待指揮官核准後改 |
| 2 | `changelog-drafter.yml` / `deploy-pages.yml` 觸發分支改為 pattern（`claude/**` 或 main） | Claude | 同上 |
| 3 | MORNING_BRIEF.md 標記 DEPRECATED 或直接由 loop 自動生成 | Claude | 同上 |
| 4 | `/handoff` 重跑一次，交接書更新到 2026-07 | Claude | 同上 |
| 5 | `.env` 填 ANTHROPIC_API_KEY（DevSwarm 復活） | **Ivan** | 🔴 |
| 6 | claude.ai 連接器授權 3 個待授權 MCP server | **Ivan** | 🔴 |
| 7 | claude.ai 介面檢查殘留雲端 Routines（本 session 讀不到） | **Ivan** | 🟡 |
| 8 | TIGER LINE 開始累積實戰 match 紀錄（每場都跑 `tiger review`） | Ivan+Claude | 🟡 |
| 9 | 清理 GitHub 上 32 條 `claude/*` 舊分支（已合併/已棄置者） | Ivan+Claude | 🟢 |
