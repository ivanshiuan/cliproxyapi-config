# 18 · Competitor & Tooling Watch

> 每季度回訪一次。列出**評估過但未採用**的外部專案/工具、當下的判斷理由、下次回訪時要重新檢視的關鍵指標。
> 目的：避免同一個 repo 被反覆重新評估（浪費 token），也避免錯過真正變成勢在必行的變化。

**上次盤點**：2026-07-02
**下次回訪**：2026-10-02（+90 天）

---

## A. 競品框架（不採用、不合作）

### A.1 `swarmclawai/swarmclaw` — 通用 self-hosted AI agent runtime

- **技術棧**：TypeScript / Next.js / Electron / SQLite
- **License**：MIT
- **社群**：598★（2026-07 快照）、v1.9.40、活躍
- **重疊面**：多 agent 編排、agent memory、skill 學習、delegation
- **採用價值**：低
  - 技術棧完全不同（TS vs Python/LangGraph），採用 = 重寫 DevSwarm
  - 通用 runtime，不是專用 code-gen swarm — 換掉會失去 workspace/sandbox/self-heal loop
  - 不解決我們真正的護城河（F&B 領域、發票、勞檢、LINE、POS）
- **參考價值**：小
  - 他們的 **runtime skill / conversation-to-skill learning** 是有趣的 prior art
  - 若我們要進化 DevSwarm 的 skill 機制，可回頭讀他們的設計（不引程式碼）
- **合作可行性**：不建議
  - 領域正交（水平框架 vs 垂直 F&B OS）
  - 商業模式衝突（OSS 生態 vs 專有垂直客戶）
  - 貢獻 F&B 特化功能等於免費把差異化送給潛在對手
- **下次回訪要看**：
  - v2.x 是否釋出 skill/memory 的重大重構
  - Anthropic 或 Google 是否併入官方生態

### A.2 `1Panel-dev/ClawSwarm` — OpenClaw 生態多 agent 群聊

- **技術棧**：Python / FastAPI / Vue3 / Docker
- **License**：**GPL-3.0**（copyleft，會感染衍生程式碼 — 決策級地雷）
- **社群**：264★、v1.0.5、dev 分支活躍、1Panel-dev 母公司
- **採用價值**：零
  - GPL 直接崩掉我們的專有 licence
  - 「多 agent 群聊」跟 DevSwarm 的「流水線 code-gen」是不同問題類別
  - 完全綁 OpenClaw，我們沒用 OpenClaw
- **參考價值**：極小 — 群聊 orchestration 跟我們的核心需求無關
- **合作可行性**：不建議 — 商業模式與生態綁定都不對齊
- **下次回訪要看**：licence 是否轉為 LGPL 或 Apache（若轉，才有話說）

---

## B. 本機開發工具（已安裝、非專案依賴）

### B.1 `rtk-ai/rtk` — Rust Token Killer（CLI proxy 省 token）

- **定位**：CLI proxy，攔截並過濾 `git status` / `pytest` / `docker ps` 等常見指令輸出後再餵給 LLM
- **省 token**：常用指令省 60–90%
- **License**：Apache-2.0
- **社群**：67.8k★（2026-07 快照）、v0.43.0、develop 分支活躍

#### Ivan 本機安裝指令（複製貼上就跑）

前提：本機有 Rust toolchain（`command -v cargo`）。若無，先跑：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
source "$HOME/.cargo/env"
```

安裝 RTK + 關遙測（一次做完）：

```bash
cargo install --git https://github.com/rtk-ai/rtk --locked
rtk --version                                                # 驗證
rtk telemetry disable                                        # 關遙測（第一道）
echo 'export RTK_TELEMETRY_DISABLED=1' >> ~/.bashrc          # 關遙測（第二道，鎖死）
# 若用 zsh 就寫進 ~/.zshrc
```

小樣測試（在你的 repo 目錄）：

```bash
rtk git status         # 對比原始 git status 的輸出長度
rtk gain               # 幾天後回來看累積省了多少 token
```

**完整移除**（想拿掉時）：`rtk init -g --uninstall`

#### 邊界規則（重要）

- ❌ 不加 rtk 進 `pyproject.toml`
- ❌ 不加 rtk 進 `Makefile`
- ❌ 不進 `docker-compose*.yml` 或 `Dockerfile`
- ❌ 不進 CI（`.github/workflows/*.yml`）
- ❌ 不改 DevSwarm / restaurant_api 任何檔案
- ✅ 只當本機開發環境的個人工具

#### 對我們的影響

- **DevSwarm 自己的 Anthropic SDK 呼叫不會被 RTK 攔截**（直接 API call、不走 shell）
- 但**我們用 Claude Code 開發 restaurant_api / DevSwarm 時的 session** 會省很多 token
- Ivan 帳單直接受惠

#### 狀態

- **本機**：待 Ivan 執行上述指令
- **Claude Code 雲端容器（如 code.claude.com 開的 session）**：**無法自動安裝**
  - 這裡的 egress policy 擋 `github.com` 的 git fetch（403）— 是設計上的組織政策，不繞道
  - 也就是說：Claude Code 雲端容器裡的 Bash 指令**不會**被 RTK 過濾
  - 若未來想在雲端 session 也享受 RTK：可探索在 SessionStart hook 內從 crates.io（proxy 有放行）或私有鏡像下載
  - 但通常 Ivan 本機才是主要開發環境，這條路先不做

---

## C. Claude Code Skill 資源（已裝 vs 收書籤 vs 不裝）

### C.1 已安裝：`addyosmani/agent-skills` — 24 個生產級工程 Skill

- **作者**：Addy Osmani（ex-Google Chrome DevRel）
- **社群**：68.5k★、MIT、v0.6.2、活躍
- **內容**：SDLC 全流程（Define → Plan → Build → Verify → Review → Ship）
- **本質**：純 Markdown prompt/instruction，不跑程式碼、不改檔案、不需 API key
- **安裝方式**（Ivan 需在自己 Claude Code 互動 session 執行）：
  ```
  /plugin marketplace add addyosmani/agent-skills
  ```
- **對我們最有用的 skills**：
  - `debugging-and-error-recovery`（Five-step triage：reproduce → localize → reduce → fix → guard）
  - `test-driven-development`
  - `code-review-and-quality`
  - `security-and-hardening`
  - `git-workflow-and-versioning`
- **跟專案客製 skill 的關係**：正交、不衝突
  - 專案 skill（`check` / `handoff` / `morning` / `spec` / `swarm` / `deploy-sid` / `match-intel`）= 專案特化
  - Addy skill = 通用工程實踐

### C.2 不安裝：`hmohamed01/Claude-Code-Scaffolding-Skill`

- **定位**：新專案 scaffolding（70+ 模板）
- **社群**：42★，個人 repo
- **理由不採用**：
  - restaurant_api 已 6500+ LOC、scaffolding 時機過了
  - 跟 Addy 的 `planning-and-task-breakdown` + `spec-driven-development` 覆蓋重疊
- **下次回訪要看**：如果我們要開新子專案（e.g. 獨立管理後台、LINE OA 專案），再重評

### C.3 不安裝：`ComposioHQ/awesome-codex-skills`

- **定位**：OpenAI Codex CLI 的 60+ skill 清單
- **社群**：14.5k★、Composio 官方
- **理由不採用**：我們用 Claude Code、不用 Codex；跟 Addy 高度重疊
- **下次回訪要看**：如果我們某天要跨到 Codex 生態，這裡有現成清單

### C.4 收書籤（不安裝）：`VoltAgent/awesome-agent-skills`

- **定位**：500+ 條外部 skill repo 的**連結索引**（不是安裝包）
- **社群**：27.1k★、MIT、含 Anthropic/Google/Vercel/Stripe 官方 skill
- **理由收書籤**：這是查詢目錄，不是可安裝的 skill 集合
- **使用場景**：當發現能力缺口時來搜（e.g. 「LINE Messaging API skill」、「pgvector skill」）
- **URL**：https://github.com/VoltAgent/awesome-agent-skills

---

## D. 待評估（下次回訪要看的方向）

### D.1 LINE OA Chatbot Designer Skill（沒有具體 URL）

- Ivan 提到但未給連結
- 對 `restaurant_api/integrations/line/` 這條線是**真的有戰略價值**的方向
- 目前 LINE integration 是 Stub + HTTP skeleton — 一個好的 LINE skill 可加速 webhook 流程、意圖判斷、人工接手設計
- **回訪動作**：主動去 VoltAgent index 搜「LINE」、看有沒有官方 LINE Developers 出的 skill

### D.2 Anthropic 官方 Claude Agent SDK 生態

- Ivan 的 DevSwarm 用 LangGraph 0.6 + Anthropic SDK 直接呼叫
- 若 Anthropic 官方推出更貼近的 Agent SDK / 官方 orchestration primitives，可能值得從 LangGraph 遷移
- **回訪動作**：季度看 Anthropic docs 有無「Agent SDK」等關鍵字的重大公告

---

## E. 不動的規則（cross-cutting）

無論下次回訪結果如何，以下規則不變：

1. 不引入 GPL / AGPL 授權的程式碼（會感染專有 licence）
2. 不引入非 Anthropic 的 LLM SDK 進 DevSwarm 核心（保持單一 provider 的優化空間）
3. 不為了「跟得上潮流」而重寫已跑通的模組（DevSwarm 4-agent 蜂群、restaurant_api 25-table schema）
4. 本機工具（RTK、Addy skill）**不進 pyproject.toml / Makefile / Dockerfile / CI**
