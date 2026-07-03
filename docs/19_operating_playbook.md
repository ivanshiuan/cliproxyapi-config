# 19 · 作業手冊 — 「Fable 5 Orchestrator + 分工契約」的日常實踐版

> 這是把 2026-07 這一週累積的知識 codify 成 repo 內可執行策略。
> 從 Ivan 的 8 小時實驗（Fable 5 orchestrator + Codex 5.5 executor + Gemini reviewer）
> 收斂出來的作業方式，加上 Fable 5 給我們的體檢建議、Addy Osmani 的工程 skill 大禮包、
> RTK token killer，全部整合成一份可執行手冊。

**上次校準**：2026-07-02
**下次校準**：2026-10-02（+90 天）

---

## 一句話定位

**最強模型當 orchestrator、便宜擅長的模型當 executor、異質模型當 reviewer、人類守終審。**
Fable 5 8 小時實驗實測：3 專案並跑，quota 只燒 20%、任務完成率 92%、重大 bug 洩漏 -82%、
3.2x 進度提升。**不是新技術，是舊工具在新模型能力下第一次跑進高效區間。**

---

## 四條核心信念（不可退讓）

1. **最強模型當 orchestrator，不當廉價碼農**
   - Opus 4.8 / Fable 5 只做判斷、切工單、仲裁、收斂
   - 別拿它去寫大量 boilerplate — 那是浪費最貴的推理能力
2. **便宜擅長的模型當 executor**
   - Codex 5.5 `/goal` 長模式、或 Claude Sonnet 4.6、或 subagent
   - 接窄工單、跑一段、驗證、回報
3. **異質模型當 reviewer，但 reviewer 沒決策權**
   - Gemini 3.1 Pro / AGY 3.1 Pro / code-reviewer agent
   - 只挑毛病、報 P0-P3 + 證據，orchestrator 逐條仲裁
4. **人類守終審 + 產品直覺**
   - Ivan 決定產品行為對不對、風險能不能接受、方向要不要改
   - 不逐行看 code、不當 integrator

---

## 角色與工具對應（實際 alias 表）

| 角色 | 模型 / 工具 | 啟動指令（本機 `~/.zshrc`）|
|---|---|---|
| **Orchestrator（大任務）** | Claude Opus 4.8 + `opus-harness` output style | `copus` |
| **Orchestrator（連 orchestrator skill）** | 同上 + orchestrator-mode 系統提示 | `corch` |
| **快速隨手（小事 YOLO）** | Claude 預設 + skip permissions | `cc` |
| **Executor（大量寫 code）** | Codex 5.5 `/goal` 長模式 | `codex /goal ...` |
| **Executor（Claude 內部）** | Sonnet 4.6 + Task/Agent 工具派 subagent | 在 orchestrator session 內派 |
| **Reviewer（異質）** | Gemini 3.1 Pro / AGY 3.1 Pro | 外部工具、獨立 window |
| **Reviewer（同構）** | `code-reviewer` subagent / `/code-review` skill | 在 orchestrator session 內派 |
| **Domain Expert（Taiwan F&B）** | `.claude/agents/restaurant-domain-expert` | 派 subagent |
| **Router 實作** | `.claude/agents/router-implementer` | 派 subagent（可並行多個 router）|
| **Spec 寫作** | `.claude/agents/spec-writer` | 派 subagent |
| **DevSwarm 蜂群** | 內部 4-agent（PM/Architect/Coder/QA）| `make swarm REQ="..."` |

**Alias 檔已備好**：`scripts/aliases.sh`，複製到 `~/.zshrc` 或 `~/.bashrc` 即可用。

---

## 日常流程（一日作息）

### A. 早晨進場（3 分鐘）

1. **開 session** — SessionStart hook 自動印：
   - ✅ PG / API key 檢查
   - 📝 最近 3 commit
   - 📋 COMMANDER_HANDOFF.md 開頭
   - 💡 harness / orchestrator 提醒
2. **確認焦點** — 打 `/morning` skill
3. **決定今日任務** — 進「任務分流表」

### B. 任務分流表

| 任務類型 | 走哪條 | 為什麼 |
|---|---|---|
| Typo / rename / 一行改動 | `cc` 直接跑 | orchestrator overhead > 效益 |
| 明確 error / test 紅了 | Addy `debugging-and-error-recovery` 五步驟（reproduce → localize → reduce → fix → guard）| debug 不需分工 |
| 讀外部檔案 / spec | `to_md.py` 轉再 Read | 檔案攝取規則（CLAUDE.md） |
| 新 spec 撰寫 | `spec-writer` agent | 已有 canonical pattern |
| 新 router 實作 | `router-implementer` agent（可並行多個）| 已有 4 個 canonical 範本 |
| DevSwarm 蜂群任務 | `make swarm REQ="$(cat specs/xxx.md)"` | 內部已 4-agent |
| **跨檔案 / 跨服務大任務** | **orchestrator mode** | 需要收斂機制 |
| 部署 SID | `deploy-sid` skill | 已封裝 |
| 部署 restaurant_api | `make deploy` + `docs/11_production_deployment.md` | 已 SOP |
| Taiwan F&B 規範問題 | `restaurant-domain-expert` agent | 已讀過 04/08 |

### C. Orchestrator 進場（大任務時）

**進場前檢查**（四題都 Yes 才進，任一 No 就直接跑）：
- [ ] 任務跨檔案 / 跨服務嗎？
- [ ] 有可驗證的 close 條件嗎？
- [ ] 需要 subagent 或外部 reviewer 嗎？
- [ ] 使用者要「執行」而不是「討論」嗎？

**Orchestrator 標準流程（六步）**：

```
1. 診斷（Read + Grep，不動任何檔案）
   ├── 讀相關 code + spec + HANDOFF
   ├── 確認根因、影響範圍
   └── 產出：問題陳述 + 假設清單

2. 切窄工單（GOAL/SCOPE/FORBIDDEN/ACCEPTANCE/REPORT_BACK/DO_NOT）
   ├── 一個 goal 只做一件事
   ├── 明確禁區
   └── 產出：可直接餵給 executor 的工單

3. 派工（Codex /goal 或 subagent 或 router-implementer）
   ├── 平行派時多個 subagent 一次發（同一 response 內）
   └── executor 執行、回報 diff + 測試結果

4. Review（reviewer 出手）
   ├── Gemini / AGY / code-reviewer agent
   ├── 找異質盲點、報 P0-P3 + 證據
   └── 產出：finding 清單

5. 仲裁（orchestrator 逐條）
   ├── 有證據嗎？→ 沒 → 否決
   ├── 反駁看看能否推翻？→ 能 → 否決
   ├── P0/P1 嗎？→ 是 → 派 executor 修
   └── 產出：確認 finding + fix 工單

6. 收斂驗證（測試綠 + smoke + diff review + commit 邊界）
   ├── ✅ 過 → 收，開下一 goal
   └── ❌ 不過 → 診斷卡點 → 開新 goal 針對卡點
```

### D. 收尾（一天結束，5 分鐘）

1. `make full-check`（ruff + pyright + pytest + alembic + smoke） — 綠了才收
2. `/handoff` — 更新 COMMANDER_HANDOFF.md
3. `git push` 到 feature branch
4. 決定要不要開 PR（見 CLAUDE.md PR 規則）
5. 若跨 session 有懸案 → 寫進 HANDOFF「你要做的」清單

---

## 為什麼堅持這套 — 具體好處

### 💰 對 Ivan 帳單（三方受惠）

| 項目 | 傳統單模型 | 這一套 | 差別 |
|---|---|---|---|
| Weekly quota 消耗（Fable 5）| 若當碼農約 60-80% | 只 20%（實測 8 小時 3 專案）| **-60-75%** |
| Weekly quota 消耗（Codex）| 40% | 40% | 差不多 |
| 總 token 成本 | 100% baseline | ~35% | **-65%** |
| 重工浪費（假 bug、scope creep） | 高 | 低 | 質性差異 |

**關鍵**：最貴的模型只做**判斷**、不寫大量 code；便宜的模型做重工；RTK（本機）過濾 Bash 輸出再省 60-90%。

### 🎯 對任務品質（Fable 5 8 小時實測）

| 指標 | 傳統流程 | 這一套 |
|---|---|---|
| 任務完成率 | ~35% | **92%** |
| 重大 bug 洩漏 | baseline | **-82%** |
| 卡關時間 | baseline | **-90%** |
| 進度速度 | 1x | **3.2x** |

**關鍵**：異質三方 review 抓出的真問題比同構 review 多、每輪都逼收斂避免 close 不起來。

### ⏰ 對 Ivan 時間

- 不用盯每一行 code —— orchestrator 已讀過 diff
- reviewer 找的問題已被過濾 —— 只看真陽性
- executor 的「完工報告」已被驗證 —— 只信測試綠 + smoke 過
- 一天結束你只看：測試綠嗎、diff 合理嗎、產品行為對嗎

### 🛡️ 對護城河

- 專案 skill（`check` / `handoff` / `morning` / `spec` / `swarm` / `deploy-sid` / `match-intel`）
  已客製化 Taiwan F&B 場景 —— 通用框架抄不走
- CLAUDE.md 12 條規則 + `docs/`（00-19）+ `specs/` = **外部記憶**，不靠模型 context
- Ledger append-only、audit_log、tenant scope —— 資料護城河

---

## 紀律清單（12 條，違反就是走回頭路）

1. **開 Opus 4.8 一律 `/output-style opus-harness`**（CLAUDE.md rule 11）
2. **大任務進場先跑「進場檢查表」四題** — 別把小事 over-engineer
3. **orchestrator 不寫大量 code** — 動手前先問：這該派 executor 嗎？
4. **executor 派工用工單模板**（GOAL/SCOPE/FORBIDDEN/ACCEPTANCE/REPORT_BACK/DO_NOT）
5. **reviewer 沒帶證據的 finding 直接否決** — 節省 executor 的假問題重工
6. **每一輪都要有可驗證的收斂條件** — 測試綠 / smoke / diff review / commit
7. **讀 diff、不信完工報告** — 「已完成」是文字，測試綠才是事實
8. **平行派工** — 同一 response 內多個 subagent 一次發、不串行
9. **委派過的搜尋不自己再跑一遍** — 別浪費 token 重工
10. **金錢 / ledger / tenant scope 永遠不放鬆**（CLAUDE.md「不變法則」）
11. **commit 前跑 `make full-check`** — 五道 gate 全綠才收
12. **一天結束 `/handoff`** — 跨 session 記憶靠外部檔案，不靠模型

---

## 禁令（7 條，做了就崩）

1. ❌ **別把 Fable 5 / Opus 4.8 拿去當廉價碼農** — 違反第一條核心信念，token 會爆
2. ❌ **別讓 reviewer 直接指揮 executor** — reviewer 沒決策權、假陽性會害整個任務發散
3. ❌ **別跳過 done definition 收斂條件** — 會走回 Codex 卡在最後 20% 的老問題
4. ❌ **別讓 executor 擴大 scope** — 違反工單 SCOPE/FORBIDDEN，diff 爆炸沒人敢收
5. ❌ **別讓 orchestrator 忘了讀 diff** — 完工報告是文字，不是事實
6. ❌ **別把小改動硬套 orchestrator** — over-engineer、比 executor 直接跑還慢
7. ❌ **別讓 Codex 自己當 PM** — 它做不到 close 定義，會一直「說做完了」

---

## 場景範例（四個典型任務走一遍）

### 場景 A：新加一個 router（e.g. `/menus`）

```
Ivan: 「幫我加 /menus router，跟 orders 那組類似」

Orchestrator（Opus 4.8 + opus-harness）：
1. 讀 restaurant_api/routers/orders.py + services/order_service.py + tests/routers/test_orders.py
2. 產出工單：
   GOAL: 實作 /menus GET/POST/PATCH 三端點
   SCOPE: 只碰 restaurant_api/{schemas,services,routers}/menus.py + tests/routers/test_menus.py
   FORBIDDEN: 不改 models（已有 Menu model）、不動 alembic
   ACCEPTANCE: tests/routers/test_menus.py 全綠 + 手動 curl 走過三端點
   REPORT_BACK: diff summary + 測試結果 + 剩餘風險
3. 派 router-implementer agent 執行
4. 執行完 review：派 code-reviewer agent
5. reviewer 說：「schema 沒 validate menu_id」→ orchestrator 反駁：「已在 service 層 check_menu_belongs_to_tenant」→ 否決
6. reviewer 說：「test 只跑 happy path」→ orchestrator 確認：真陽性 → 派 executor 補 edge case
7. 收：make full-check 綠 → commit → 下一 goal
```

**時間**：Fable 5 orchestrator 大約 10-15 分鐘（不含 executor 執行）
**你（Ivan）介入**：spec review、產品驗收

### 場景 B：修一個中型 bug（跨檔案）

```
Ivan: 「/orders 建單成功但 stock 沒扣，幫我修」

Orchestrator：
1. 診斷：Read orders.py + stock_service.py + services/order_service.py + BOM chain
2. 產出假設清單：
   - H1: order_service 沒呼叫 consume_bom
   - H2: consume_bom 有呼叫但 tx rollback
   - H3: BOM 表沒 seed
3. 派 spec-writer agent 寫 debug spec（不動 code）
4. 派 executor 跑 debug spec（一個一個假設驗證）
5. Executor 回：「H2 命中 — consume_bom 拋 InsufficientStock 但被 order_service 吞掉」
6. Orchestrator 派 executor 修 + 加 regression test
7. 派 code-reviewer 驗
8. 收：test 綠 + 手動下單看 stock_movements 有無記錄
```

### 場景 C：讀外部給的 spec / 大檔（e.g. Ivan 丟 PDF）

```
Ivan: 「這份 PDF 是新配合供應商的 API doc，看一下能不能 integrate」

Orchestrator：
1. 檔案攝取規則（CLAUDE.md）：PDF → make to-md FILE=doc.pdf
2. Read 轉出來的 .md
3. 產出 integration 評估：
   - 認證方式、API 端點、rate limit、資料格式
   - 跟 restaurant_api 哪些 service 整合
   - 風險 / 未知
4. 派 restaurant-domain-expert agent review 領域問題
5. 決定：可行 → 派 spec-writer 寫 spec 進 specs/
6. 不寫任何 integration code — 這回合只到 spec

Ivan 只做：看 spec、拍板進 Phase 幾
```

### 場景 D：DevSwarm 產一個新 module

```
Ivan: 「跑 specs/discount_resolver.md」

Orchestrator：
1. 確認 spec 完整（AC ≥ 10、out-of-scope 明確）
2. 檢查 .env 有 ANTHROPIC_API_KEY
3. make swarm REQ="$(cat specs/discount_resolver.md)"
4. 內部 4-agent 跑（PM → Architect → Coder → QA）
5. 蜂群回：workspace/discount_resolver/ + tests/
6. Orchestrator review workspace 產物
7. make promote TASK=discount_resolver → 搬進主 codebase
8. make full-check 綠 → commit

Ivan 只做：spec 完整度驗收、產品意圖確認
```

---

## 驗收 — 怎麼知道這套跑起來了

**session 層驗收（每次開）**：
- [ ] SessionStart hook 印出上下文（PG / commit / HANDOFF）
- [ ] `/output-style opus-harness` 可切換
- [ ] 說「大任務、orchestrator mode」— skill 被觸發

**週級驗收（每週回顧）**：
- [ ] Weekly Anthropic quota 消耗曲線下降（vs 前一週基線）
- [ ] Weekly close 任務數上升
- [ ] 週回顧時 Ivan 只需仲裁 + 拍板，沒手動 debug 過小事
- [ ] `docs/` 有新的 ADR / spec / handoff 累積

**月級驗收**：
- [ ] Phase 1 restaurant_api Phase 進度前進
- [ ] 至少一個 orchestrator-mode 大任務 close 成功
- [ ] 沒有「scope creep 到收不了尾」的任務

**季級驗收（90 天回訪 `docs/18_competitor_watch.md`）**：
- [ ] 這套 playbook 是否需修訂（新工具、新模型出現）
- [ ] 有無新競品 / 新 skill 值得評估

---

## 90 天回顧機制

**下次校準日**：2026-10-02

**要問的問題**：
1. 四條核心信念是否有需要更新（新模型出現？）
2. 角色對應是否需換人（Codex 6? Gemini 4? 新 orchestrator?）
3. 紀律清單有沒有太理想沒做到的
4. 禁令有沒有踩到、怎麼修
5. 實測數據 vs Fable 5 的 8 小時實驗基準對得上嗎

---

## 快速入場（新 session 只讀這一段）

**開 session** → SessionStart hook 印上下文 → **看任務類型分流**：

- 小事 → `cc` 直接跑
- Debug → 五步驟 triage
- 大任務 → 進 orchestrator mode（讀 `.claude/skills/orchestrator-mode/SKILL.md`）
- 沒把握 → 用 `AskUserQuestion` 給 Ivan 選項

**永遠**：commit 前 `make full-check`、收工 `/handoff`、跨 session 記憶靠檔案不靠腦袋。

---

## 相關檔案（一次看清楚）

| 檔案 | 用途 |
|---|---|
| `.claude/output-styles/opus-harness.md` | 9 條行為紀律（Opus 4.8 補強）|
| `.claude/skills/orchestrator-mode/SKILL.md` | Fable 5 分工契約 |
| `.claude/agents/*.md` | 3 個專案客製 subagent |
| `.claude/settings.json` | 4 層 hooks + 權限 |
| `scripts/session_start_hook.sh` | JIT context 注入 |
| `scripts/aliases.sh` | 本機 alias 一次設好 |
| `CLAUDE.md` | 12 條決策規則 |
| `docs/18_competitor_watch.md` | 評估過的工具（不重複評）|
| `docs/19_operating_playbook.md` | **這份 — 日常實踐版** |
| `COMMANDER_HANDOFF.md` | 跨 session 交接 |
| `MORNING_BRIEF.md` | 早晨速覽 |

---

## 最後一句

**別再讓一個模型硬幹整包工程。最強的當 orchestrator，code 丟給 Codex 寫，毛病讓 Gemini 挑，產品你自己顧。更快、更穩、更省、也更能真的交付。**

—— Ivan 8 小時實驗心得，2026-07
