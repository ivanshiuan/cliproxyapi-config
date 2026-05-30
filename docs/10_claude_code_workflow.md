# 10 — Claude Code 工作流：何時用什麼能力

> Claude Code 的「隱藏能力」全部設定在這個 repo 裡了。
> 這份文件解答：**遇到什麼狀況該用哪個能力？**

---

## 設定總表

| 能力 | 設定位置 | 此 repo 已設定 |
|---|---|---|
| 專案記憶 | `CLAUDE.md` | ✅ |
| 自訂指令 | `.claude/commands/*.md` | ✅ 5 個 (`/check`, `/swarm`, `/spec`, `/handoff`, `/morning`) |
| Subagents | `.claude/agents/*.md` | ✅ 3 個 (`spec-writer`, `router-implementer`, `restaurant-domain-expert`) |
| Hooks | `.claude/settings.json` | ✅ 3 個 (PostToolUse / SessionStart / PreCompact) |
| Permissions | `.claude/settings.json` | ✅ 43 allow + 10 ask |
| Plan Mode | 內建 (Shift+Tab×2) | 用法見下 |
| Compact | 內建 (`/compact`) | 用法見下 |

---

## 何時用什麼 — 決策樹

### 🟢 **每次開新 session（自動）**

CLAUDE.md 自動載入。`SessionStart` hook 自動：
- 啟動 Postgres（若已停）
- 檢查 `ANTHROPIC_API_KEY` 是否填寫
- 顯示 PG / API key 狀態

→ **你不用做任何事**。

### 🟢 **每次 Edit/Write `.py` 檔（自動）**

`PostToolUse` hook 自動跑 `ruff format`。

→ **你不用做任何事**。

### 🔵 **指揮官早上開工**

```
/morning
```

執行：起 DB → 跑測試 → ruff/pyright → 顯示 backlog → 列出 blockers → 推薦下一步。

### 🔵 **commit 前**

```
/check
```

跑 ruff + pyright + pytest + alembic-check + db-smoke。一行 verdict。

### 🔵 **想跑 DevSwarm 任務**

```
/swarm specs/bom_consumer.md      # 用既有 spec
/swarm "Build a Taiwan 身分證 validator"   # 即時 request
```

自動加 `--budget 5.0` 預算煞車。跑完 ask 是否 `make promote`。

### 🔵 **想寫新的 DevSwarm spec**

```
/spec invoice_lottery_checker
```

調用 `spec-writer` agent，照 `profit_calc.md` pattern 產 spec。

### 🔵 **想更新指揮官 to-do**

```
/handoff
```

掃 git log + backlog + .env + DB 狀態，重寫 `COMMANDER_HANDOFF.md`。

---

## 進階：Subagent 何時用

| 想做的事 | 用哪個 agent | 怎麼觸發 |
|---|---|---|
| 寫新 DevSwarm spec | `spec-writer` | `/spec <name>` 自動調用 |
| 實作新 FastAPI router | `router-implementer` | 我說「並行 4 agent 寫 4 routers」自動分派 |
| 食安/勞檢/發票/POS 域問題 | `restaurant-domain-expert` | 我說「問領域長 X 規則怎麼跑」 |
| 通用研究 / 探索 | `general-purpose` | 我說「研究 X」 |
| 規劃複雜實作 | `Plan` | 「先規劃這個再實作」 |
| 找特定符號/檔案 | `Explore` | 「我要找 X 在哪定義」 |

**並行多 agent**：當多檔工作彼此獨立（如 4 個 router 各自 schemas+service+test）時，叫 Claude 「並行派 4 個 agent」。speedup 可達 25-30×。

---

## 進階：Plan Mode

**何時用：**
- 規劃超過 3 個檔案的變更
- 不確定該動哪個檔案、想先 review 設計
- 跨模組重構

**怎麼用：** 按 `Shift+Tab` 兩次進入 Plan Mode。Claude 只讀不寫，產出計畫後等你 approve 才執行。

**不要用：**
- 一句話能解決的小修改（直接動手更快）
- 純查詢類問題（用 Explore agent 更快）

---

## 進階：Compact

**何時用：**
- session 進行超過 50K token，response 變慢
- 換主題（從寫 router 切到 review schema）
- 重要決策已下、不想被前面 noise 沖淡

**怎麼用：** `/compact <可選的指示>`。
建議指示：「保留：技術決策、API contract、未完成的 TODO；丟棄：tool output、debug 步驟」。

**PreCompact hook** 會自動記錄時間到 `.claude/compact-log.txt`，方便你事後追溯哪次 session 被 compact 過。

**不要用：**
- session 剛開、context 還很少時
- 你不確定 Claude 是否會丟掉關鍵資訊（先 `/handoff` 把狀態存 doc 再 compact）

---

## 進階：什麼時候 Claude 會卡住

| 症狀 | 原因 | 解法 |
|---|---|---|
| 一直問同樣的問題 | session 太久、忘記前面決策 | `/compact` 或開新 session |
| 改錯方向、改不對 | 沒先 Plan Mode | 重來，這次 Shift+Tab×2 |
| 改完測沒過 | 沒跑 `/check` | 修完手動跑 `/check` |
| 改了 schema 沒同步 migration | 沒看 CLAUDE.md 提醒 | `cd restaurant_api && alembic check` |
| 寫 router 用了 sync TestClient | 沒讀 conftest.py 警告 | CLAUDE.md 列了，重提醒 Claude 看 |
| DevSwarm 燒錢 | 沒加 budget | `/swarm` 自動加 `--budget 5.0`；直接呼叫加 `--budget 2.0` |

---

## 我給你的「Claude Code 用法 SOP」

### 📅 每天開工
1. 開 Claude Code
2. SessionStart hook 自動跑 → 看 PG / API key 狀態
3. 你輸入 `/morning` → 看當下狀況 + 推薦下一步
4. 照建議動

### 🛠️ 開始一個新功能
1. 用 Plan Mode（Shift+Tab×2）讓 Claude 先讀 + 規劃
2. Review 計畫，approve
3. Claude 動手；每個 .py 編輯自動格式化（PostToolUse hook）
4. 寫完跑 `/check`

### 🚀 跑 AI 蜂群任務
1. `/spec <name>` 或人工寫好 spec
2. `/swarm specs/<name>.md` —— 自動加 `--budget 5.0`
3. 跑完 review `workspace/<task_id>/` 產出
4. 滿意 → 接受 `make promote TASK=<id>` 提示
5. `/check`、commit、push

### 📈 Session 變長變慢
1. 重要決策先寫進 `docs/*.md` 或 CLAUDE.md（持久化）
2. `/compact` with 指示：「保留 X、Y、Z，丟棄 debug 過程」
3. 繼續

### 🆘 卡住
1. 先看 `docs/07_devswarm_runbook.md`（DevSwarm 卡住）
2. 或 `docs/08_safety_compliance.md`（食安/合規問題）
3. 或叫 `restaurant-domain-expert` agent（域問題）
4. 都不行 → 新開 session，把當下狀況直接貼

---

## 重要原則

1. **設定一次，受惠永久。** Hooks 在 `.claude/settings.json`、commands 在 `.claude/commands/`、agents 在 `.claude/agents/` — 全 commit 進 git，下次開 session 自動載入。
2. **記憶在 CLAUDE.md。** 不變的東西寫這、會變的寫 `COMMANDER_HANDOFF.md` 或 `docs/`。
3. **Plan Mode 是免費保險。** 一句話能解決的小事不用，但大改動值得花 30 秒先看計畫。
4. **Compact 不是萬靈丹。** 與其等 session 變慢，不如先把決策寫進 doc。
5. **Subagent 是分工不是擋箭牌。** 同樣是 Claude，但給它縮小的 context + 明確的 scope，會比一個大 agent 邊讀邊做更穩。
6. **Hook 別包山包海。** 一個 hook 只做一件事，PostToolUse 自動 format 就好、別塞測試（測試該 `/check` 主動跑）。

---

## 給未來的我

如果你 6 個月後忘記怎麼用：

```
/morning       # 看當下狀況
/check         # 確認沒爆
cat CLAUDE.md  # 看不變法則
cat COMMANDER_HANDOFF.md  # 看你還沒做什麼
```

這四個指令是入口。其他都是工具。
