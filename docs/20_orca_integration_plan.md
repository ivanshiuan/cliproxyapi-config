# 20 — 霸虎OS × Orca：Agent 工作台整合規劃

**Status:** 規劃提案（待指揮官核准後啟動 Phase O-0）
**Scope:** 把 [Orca](https://github.com/stablyai/orca)（開源 ADE，MIT，42k+ stars）整合進霸虎OS，成為「指揮一支 AI 工程隊」的統一工作台。
**Out of scope:** DevSwarm 內部 agent 拓撲（見 `docs/02`）、RestSwarm 業務功能（見 `docs/06`）。
**上游知識卡:** [`knowledge/2026-08-11-orca-multi-agent-workbench.md`](./knowledge/2026-08-11-orca-multi-agent-workbench.md)

> **霸虎OS** = 本專案整體作業系統的統稱：DevSwarm（AI 工廠）+ RestSwarm（餐飲後端）+
> Claude Code 指揮台 + 帝國大腦（docs/knowledge）。本文規劃 Orca 進來後坐在哪一層、怎麼接。

---

## 一、定位判斷（最重要的一段）

**Orca 不是取代任何現有零件，它是補上霸虎OS 一直缺的那一層：編排台（Orchestration Deck）。**

現況的真實痛點：

| 痛點 | 現在怎麼撐 | 撐法的極限 |
|---|---|---|
| 多任務平行時看不到全局 | 開多個 Claude Code session + GitHub 通知 | session 之間互相看不見，靠指揮官腦內拼圖 |
| DevSwarm 產出審查 | `workspace/<task_id>/` + `make promote` 手動搬 | 產出不是 diff、不能比較、搬移是單行道 |
| 同一需求想比較多種方案 | 做不到（一次一個實作） | 關鍵模組（損益、發票）沒有第二意見 |
| 手機指揮 | GitHub mobile 審 PR + Hermes/LINE 通知 | 只能審終點（PR），看不到過程、不能中途追加指令 |

Orca 的四個核心能力恰好逐一對上：**平行 worktree 隔離、統一 diff 審查佇列、同需求多 Agent 比稿、手機監看+追加指令**。

**整合原則（四條，違反任何一條就是做錯了）：**

1. **Orca 是「駕駛艙」，不是「引擎」** — DevSwarm 五角色蜂群、Codex 不變法則、budget guard 全部保留原樣；Orca 只負責「開幾個工作面、看 diff、下指令」。
2. **GitHub PR 仍是唯一正式閘門** — Orca 裡的 review 是「快速迭代審查」；要進 main 一律走 PR + Ivan 按 Merge，`make full-check` 照跑。Orca 不碰 merge 權。
3. **任何 Orca 管理的 agent 都拿不到 production credentials** — 與 `docs/02` §12 威脅模型同一條紅線。
4. **交付自動化、不交付 runbook**（CLAUDE.md 法則 11）— 每個 Phase 對指揮官只暴露**一條 idempotent 指令**與**一個 approval 點**；所有步驟由腳本吃掉，重跑不炸。自動化本體已隨本規劃一併交付（`scripts/orca/` + make 目標），不是未來式。

---

## 二、現實約束（先講清楚，避免規劃落空）

1. **Orca 是 Electron 桌面 app**（macOS/Windows/Linux）+ React Native 手機 companion。
   它裝在**指揮官的 Mac/PC**，不是裝在雲端 Claude Code Remote session 裡。
   雲端 CCR session（像現在這個）與 Orca 是**平行的兩條指揮通道**，不衝突。
2. **遠端執行走 SSH worktrees**：Orca 支援讓 agent 在遠端主機跑（自動重連、port forwarding），
   也有 headless Linux server 模式與 CLI（`orca worktree create` / `snapshot` 等）。
   → DevSwarm 未來可以在一台 VPS 上跑，由 Mac 上的 Orca 指揮、手機監看。
3. **比稿 = 花 N 倍錢**：同 spec 餵 3 個 agent 就是 3 倍成本。必須有分級規則（見 §五）。
4. **官方文件站（onorca.dev）在本 CCR 環境被網路白名單擋** — 安裝與細部設定要在
   Ivan 本機做（或把 `onorca.dev` 加進白名單，見 `docs/18` 的網路設定 SOP）。

---

## 三、目標架構（Orca 進來後的霸虎OS 分層）

```
┌─ 指揮層（人）────────────────────────────────────────────┐
│  Ivan：手機（Orca companion + GitHub mobile + LINE 通知）    │
│        桌面（Orca workbench / Claude Code / GitHub）         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─ 編排層（Orca = 新增的這一層）───────────────────────────┐
│  · 平行 git worktrees（一任務一 worktree，互不踩檔）         │
│  · 統一 review 佇列（AI diff 上留言 → agent 續改）           │
│  · 同 spec 多 agent 比稿（bake-off）                         │
│  · SSH 遠端執行 + 手機通知/追加指令                          │
└──────┬───────────────┬───────────────┬─────────────────┘
       ▼               ▼               ▼
┌─ 執行層（agents，都是 CLI，Orca 通吃）─────────────────────┐
│  Claude Code CLI   DevSwarm CLI        （可選）Codex 等       │
│  （互動式改碼）    （python -m devswarm  第三方 agent 比稿    │
│                     --task-file spec）   用，需另訂閱）       │
└──────┬───────────────┬───────────────┬─────────────────┘
       ▼               ▼               ▼
┌─ 守門層（不變，Orca 不得繞過）──────────────────────────┐
│  make full-check（ruff+pyright+pytest+alembic+smoke）        │
│  Codex 不變法則 / budget guard / GitHub PR + Ivan 按 Merge   │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─ 資產層 ────────────────────────────────────────────────┐
│  本 repo（RestSwarm + DevSwarm + docs/knowledge 帝國大腦）   │
└─────────────────────────────────────────────────────────┘
```

**新舊機制對應表**（哪些被升級、哪些保留）：

| 現有機制 | Orca 進來後 | 處置 |
|---|---|---|
| `workspace/<task_id>/` 沙盒目錄 | git worktree（一任務一 worktree） | **升級**：產出天生是 diff，可審可比可丟 |
| `scripts/promote.py` 搬檔 | worktree 審完直接 merge 進分支 → 開 PR | **升級**：Phase O-1 驗證後退役 promote |
| 多開 Claude Code session | Orca 一個畫面管全部 worktree | **升級**：全局可視 |
| Hermes（LINE/console 通知） | 與 Orca 手機通知**並存** | **保留**：Hermes 推「終局事件」給 LINE；Orca 推「過程」給 companion |
| GitHub PR + Ivan 審 | 不變 | **保留**：唯一正式閘門 |
| DevSwarm 內部 rlimits 沙盒 | 不變（worktree 是外圈、rlimits 是內圈） | **保留**：兩層隔離疊加 |
| `/swarm` command + budget guard | 變成 Orca 裡的一個 custom agent 入口 | **保留**：預算煞車照掛 |

---

## 四、分階段路線（每階段都有明確驗收與撤退鍵）

> 命名 O-0 ～ O-4，與 `docs/06` 的 RestSwarm Phase 編號脫鉤，避免混淆。
> 原則：**每一階段獨立有價值**，隨時可以停在該階段不往下走，不會留爛尾。

> **每個 Phase 對指揮官只有：一條指令 + 一個 approval 點。**
> 自動化本體在 `scripts/orca/`（隨本規劃已交付），make 目標見 `make help`。

### Phase O-0 — 試駕（半天，$0）

| | |
|---|---|
| **指揮官跑** | `make orca-bootstrap`（在 Mac 上，一條，idempotent） |
| **自動化做** | 檢查/安裝 Orca、驗 git worktree / `.venv` / API key / PG、印出 DevSwarm custom-agent 註冊指令 |
| **approval 點** | Orca 首次開啟時按「加入 custom agent」確認（一次性） |
| **驗收** | bootstrap 全綠 + 在 Orca 看到兩個 worktree 平行跑小任務、diff 上能留言續改 |
| **撤退鍵** | 不順手 → 解除安裝，本規劃 `status: archived`，零沉沒成本 |

### Phase O-1 — DevSwarm 上台（已自動化，隨用隨跑）

| | |
|---|---|
| **指揮官跑** | `make swarm-wt SPEC=specs/bom_consumer.md PUSH=1` |
| **自動化做** | 建/重用 worktree（`swarm/<spec>` 分支）→ 跑蜂群 → `promote`（含兩道 pytest gate）→ ruff gate → commit → push → 開 PR |
| **approval 點** | 審 PR、按 Merge（唯一人工動作） |
| **驗收** | 一條 spec 從指令到 PR 全程零手動搬檔；重跑同指令不炸（idempotent） |
| **技術注意** | worktree 共用主 repo `.venv`；`workspace/` 舊流程雙軌保留兩週，穩了才退役 `promote.py`。失敗時產出留在 worktree，修 spec 後重跑同一條指令續作；`DRY_RUN=1` 先看 PRD、`FRESH=1` 砍掉重練、`SETUP_ONLY=1` 只備 worktree 給 Orca 掛其他 agent |

### Phase O-2 — 關鍵模組比稿（$10–15/次，僅 S 級 spec）

| | |
|---|---|
| **指揮官跑** | `make bakeoff SPEC=specs/profit_calc.md` |
| **自動化做** | 每個 lane 一個 worktree（devswarm 蜂群 / claude headless；第三方 lane 等有訂閱再加）→ 各自實作+測試+commit → 產出比稿報告 `docs/knowledge/<日期>-bakeoff-<spec>.md`（diff 規模、審查指令、rubric 骨架） |
| **approval 點** | 讀報告 → 挑贏家分支 → 審該分支 PR；輸家 `make wt-clean` 清場 |
| **rubric** | Codex 不變法則逐條（MONEY-001 等）+ spec AC 逐條 + 測試綠；裁判用乾淨 Claude session（不看實作過程、只看 diff — 與 `docs/02` §2.1 Reviewer 隔離同一哲學） |
| **預算規則** | 見 §五分級表 — 只有 S 級 spec 才比稿 |

### Phase O-3 — 遠端 + 手機指揮（1 天 + VPS 成本）

| | |
|---|---|
| **指揮官跑** | （VPS 上）`git clone … && make install && make orca-bootstrap` — 同一條 bootstrap，Linux 分支自動走 headless 檢查 |
| **自動化做** | 就緒檢查同 O-0；之後 Orca SSH worktrees 指向該機，蜂群在遠端燒，Mac 可闔蓋 |
| **approval 點** | 手機 Orca companion 上的中途決策 + 最終 PR Merge |
| **紅線** | VPS 只放 `ANTHROPIC_API_KEY`，**絕不放 production credentials** |
| **通知分工** | Orca companion = 過程（卡住、要人決策）；Hermes→LINE = 終局（succeeded / exhausted / budget_halted，`docs/02` §13.1） |
| **撤退鍵** | 不養 VPS → 停在 O-2 本機執行，手機指揮用現有 GitHub mobile + CCR |

### Phase O-4 — 制度化（持續，由 Claude 維護、非指揮官作業）

1. `CLAUDE.md`「直接交付指令對照表」加：「比稿這個 spec」→ `make bakeoff`。
2. `docs/07` runbook 加 Orca 故障排除節（worktree 殘留 → `make wt-clean`、SSH 斷線）。
3. 雙軌滿兩週後：退役 `promote.py` 直呼路徑、`workspace/` 降級為 DevSwarm 內部暫存。
4. 每季回顧比稿報告 → 調整「哪類任務給哪個 agent」的派工默契（帝國大腦吃自己的狗糧）。

---

## 五、比稿預算分級（防止「為了比而比」燒錢）

| 級別 | 定義 | 例子 | 策略 | 上限 |
|---|---|---|---|---|
| **S** | 錢/帳/法遵，錯了有真實損失 | profit_calc、發票驗證、COGS | 2–3 agent 比稿 + 獨立裁判 | $15/spec |
| **A** | 核心業務邏輯 | discount_resolver、BOM | DevSwarm 單跑（五角色已含對抗審查） | $5/spec |
| **B** | 樣板/膠水/文件 | router 樣板、docs | Claude Code 直接做，不進蜂群 | $2/task |

月度總額仍受 CLAUDE.md 既有規則約束（< $50/月）；比稿費用計入同一池。

---

## 六、風險與紅線

| 風險 | 等級 | 對策 |
|---|---|---|
| 第三方 agent（Codex 等）讀到敏感檔 | 高 | worktree 只含 repo 內容；`.env` 本來就不進 git；比稿機器不放 prod credentials |
| Orca 更新頻繁、介面/設定破壞性變更 | 中 | 我們只依賴其最穩定的三能力（worktree/review/ssh）；custom agent 是純 CLI 呼叫，耦合極淺 |
| worktree 殘留吃磁碟 | 低 | O-4 在 runbook 加清理 SOP（`git worktree prune`） |
| 比稿成癮、預算失控 | 中 | §五分級表 + 既有 budget guard；S 級以外不比 |
| 繞過 PR 直接 merge 的誘惑 | 中 | 原則 2 白紙黑字；Orca 內不設 main 的推送權（分支保護不動） |
| 對 Orca 產生指揮依賴、Orca 停更 | 低 | 底層全是 git worktree + CLI，Orca 消失也能用裸 git 指令跑同流程 |

---

## 七、需要指揮官拍板的三件事

1. **啟動時機**：現在就跑 O-0 試駕（半天、零成本），或等下一個 RestSwarm 里程碑後？
   → 建議：**現在**。O-0 不動任何現有系統。
2. **比稿名單**：只用 Claude 系（Claude Code + DevSwarm，同一把 API key），
   或加第三方 agent（Codex 等，需另外訂閱/金鑰）？
   → 建議：先 Claude 系跑通 O-2，第三方等有訂閱再加，不為比稿專門買。
3. **O-3 的遠端機**：要不要養一台 VPS？（月成本約 $5–20，換來「手機隨時指揮、Mac 不用開」）
   → 建議：O-2 驗證有感之後再決定，不預先買。

---

## 八、與現有文件的關係

| 文件 | 關係 |
|---|---|
| `docs/00_vision.md` | 上游 SSOT；Orca 屬 Layer 1（DevSwarm/工廠側）的指揮介面升級 |
| `docs/02_devswarm_architecture.md` | 蜂群內部不動；本文只改其「外殼」（落點 workspace/ → worktree，§7 沙盒兩層化） |
| `docs/06_execution_plan.md` | 平行軌道；Orca 整合不佔用 T1 十二任務的排程 |
| `docs/07_devswarm_runbook.md` | O-4 時補 Orca 故障排除節 |
| `docs/10_claude_code_workflow.md` | Claude Code 用法不變；Orca 是再上一層的多 session 指揮台 |
| `docs/18_browser_act_setup.md` | 若要在 CCR 讀 onorca.dev 文件，白名單 SOP 在此 |
| `knowledge/2026-08-11-orca-multi-agent-workbench.md` | 本規劃的觸發知識卡 |
