---
source_type: post
source_url: https://github.com/stablyai/orca
captured_at: 2026-08-11
tags: [ADE, 多Agent編排, git-worktree, Claude Code, 開發工作流]
applies_to: [系統與AI]
status: inbox
---

# Orca：管理一支「AI 工程隊」的開源工作台（ADE 趨勢）

## 一句話重點
IDE 的下一代不是「編輯器塞聊天框」，而是 ADE（Agent Development Environment）——人負責拆任務、配置 Agent、審查結果；多個 Coding Agent 在各自隔離的 git worktree 平行施工。

## 核心重點（3–5 條）
- **Orca 是什麼**：開源（MIT）多 Coding Agent 管理工作台，支援 40+ CLI Agent（Claude Code、Codex、OpenCode、Cursor CLI 等），查證時已 42.2k stars（貼文當下 3.8 萬），更新頻率極高。
- **隔離機制**：每個 Agent 在獨立 git worktree 工作 → 同一需求可同時交給多個 Agent，比較方案後擇優合併，互不踩檔。
- **人的角色**：Review AI 產生的 diff、在程式碼上留言讓 Agent 續改、從手機監看進度 / 收通知 / 追加指令、透過 SSH 讓 Agent 在遠端主機施工。
- **技術架構**：Electron（桌面）+ React Native（手機）+ TypeScript；終端機用 WebGL 渲染。
- **核心洞見**：未來最有生產力的開發者不是打字最快的，而是最會**拆任務、配置 Agent、審查結果、整合系統**的人——Coding 從「親自施工」變成「指揮 AI 工程隊」。

## 可用在帝國哪個環節（So what）
Ivan 現在的工作模式**就是 Orca 想服務的那種人**：指揮官不看程式碼、只在 GitHub（含手機）審 PR 按 Merge——這條路線被一個 42k stars 的專案驗證了。具體可借鑑三點：

1. **DevSwarm 沙盒可升級成 git worktree 隔離**：目前 DevSwarm 產出走 `workspace/`（gitignored）+ `make promote` 搬移；Orca 證明 worktree 是更乾淨的隔離單位——每個任務一個 worktree，diff 直接可審、可比較、可丟棄，不用手動搬檔。
2. **「同一 spec 餵多個 Agent 比稿」是可行戰術**：關鍵 spec（例如損益計算）可平行跑多份實作，審 diff 擇優——與 Claude Code 的 judge panel / worktree isolation 能力天然對齊，成本換信心。
3. **手機指揮工作流不用自建**：Orca 的 mobile companion（監看、通知、追加指令）與 Ivan 現行「手機審 PR」互補；若某天 Claude Code 遠端 session 不夠用，Orca 是現成的備選指揮台，不必自研。

## 行動項（Next action）
- [ ] 下次 DevSwarm 迭代時，評估用 git worktree 取代 `workspace/` + `promote.py` 的搬移流程（一任務一 worktree，審完 merge 或丟棄）。

## 原文摘錄 / 逐字稿重點
> 它不是又一套把聊天框塞進編輯器的 AI IDE，而是一個用來管理「多個 Coding Agents」的工作台。

> 每個 Agent 都在獨立的 Git worktree 裡工作……把同一個需求同時交給多個 Agent、比較不同 Agent 寫出的方案、直接 Review AI 產生的 Diff、在程式碼上留言讓 Agent 繼續修改、從手機查看進度、透過 SSH 讓 Agent 在遠端主機上工作。

> 當一個開發者開始同時管理 5 個、10 個，甚至更多 AI Agent 時，要用什麼介面管理這支數位工程團隊？

> 未來最有生產力的開發者，不一定是打字最快的人，而是最懂得拆任務、配置 Agent、審查結果與整合系統的人。

（查證補充，2026-08-11：GitHub 實際數據 42.2k stars / 2.9k forks；官方定位 "AI orchestrator for the 100x developer"；MIT License。）
