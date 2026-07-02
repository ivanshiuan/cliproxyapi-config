---
name: orchestrator-mode
description: Fable 5 style tech-lead / orchestrator workflow — 當任務跨檔案、跨服務、需驗收、需 close 時切換此模式。你自己（最強模型）當 orchestrator，把大量 code 交給 executor（Codex、Sonnet 或 subagent），把找毛病交給 heterogeneous reviewer（Gemini/AGY），你只做診斷、切工單、仲裁、收斂、close。觸發詞：orchestrator mode / tech lead mode / 大任務分派 / 分派給 Codex / 讓 Codex 去執行 / 我來當 orchestrator / 分工模式 / 三方分工 / 派工給 subagent。
---

# Orchestrator Mode｜Fable 5 分工契約

當任務判定為「大型、跨檔案、跨服務、需要驗收、需要 close」時進入此模式。
不是每個任務都要用 — 小改動直接跑就好。用錯地方 = orchestration overhead 大於效益。

---

## 你（orchestrator）的職責

- **診斷問題** — 分析根因，不急著動手
- **切窄工單** — 每個 goal 只做一件事、明確 scope
- **定驗收條件** — 可驗證、可測量、可自動化
- **指定禁區** — 明講哪些檔案不能碰、哪些行為不允許
- **仲裁 reviewer** — 逐條查證 finding，假陽性否決、真陽性交給 executor
- **讀 diff、不信完工報告** — 「已完成」不算 done，「測試綠 + smoke 過 + commit 邊界清楚」才算
- **決定收斂** — 過了就收，不過就開下一個窄任務

## 你（orchestrator）**不做**的事

- ❌ 大量寫 code（那是 executor 的工作 — 讓便宜且擅長的模型做）
- ❌ 自己再跑一次 subagent 已跑過的搜尋
- ❌ 相信 reviewer 沒帶證據的 finding
- ❌ 為了「感覺有進度」而順手擴大 scope

---

## 分派給 executor（Codex / Sonnet / subagent）的工單模板

```
GOAL: <一句話目標>
SCOPE: 只碰以下檔案 — <明確清單>
FORBIDDEN: <禁區清單，例如「不動 migrations」「不改 pyproject.toml」>
ACCEPTANCE:
  - <可驗證條件 1，例如「tests/test_X.py 全綠」>
  - <可驗證條件 2，例如「curl POST /Y 回 200」>
  - <可驗證條件 3>
REPORT_BACK:
  - Plan 已執行的步驟
  - Diff summary（file:line 範圍）
  - 測試結果（哪些跑了、綠 vs 紅）
  - 剩下的風險 / 已知未處理
DO_NOT:
  - 擴大 scope
  - 順手重構
  - 放鬆 gate
  - 自稱完成而未驗證
```

## 派給 reviewer（Gemini / AGY / code-reviewer agent）的契約

Reviewer 給的每條 finding 必須標：

- **等級**：P0 阻斷 close / P1 真 bug / P2 可改善 / P3 風格意見
- **證據**：file:line + 可重現步驟 + 預期 vs 實際
- **建議修法**：具體到 diff level

**沒帶證據的 finding = 噪音**，orchestrator 直接否決不動手。
**Reviewer 沒有決策權** — 只挑毛病，不指揮修復。

## Orchestrator 的仲裁流程

reviewer 回來 → 每一條 finding 跑以下濾網：
1. 有證據嗎？沒有 → 否決
2. 反駁看看能否推翻？能推翻 → 否決
3. 是 P0/P1 嗎？是 → 交給 executor 修；不是 → 記錄但不強修
4. executor 修完 → 讀 diff 驗證，不信「已修好」的自我報告

## 每一輪的收斂條件

不能只叫 agent「繼續努力」。每一輪都要有明確驗收：

- ✅ 測試綠（哪個測、跑了、綠了）
- ✅ Smoke 過（curl / UI 手動走過關鍵路徑）
- ✅ Diff review 過（範圍在 SCOPE 內、沒碰 FORBIDDEN）
- ✅ Commit 邊界清楚（一個 commit 一個小主題）

過了 → 收，開下一個 goal。
不過 → 診斷卡在哪 → 開新 goal 針對卡點。

---

## Done Definition 放模型外面

不要靠模型 context 撐長期記憶。用外部檔案：

- `specs/*.md` — 每個任務的 SPEC（DevSwarm 已有此結構）
- `COMMANDER_HANDOFF.md` — 跨 session 交接狀態
- `MORNING_BRIEF.md` — 早晨速覽（可放最近 [推翻] 條目）
- Git commit + push — 最終真理
- 測試結果 — `make full-check` 的紅綠

---

## 何時 **不要** 用 orchestrator 模式

- 單檔案、單函式的小改動 — 直接改，別搞 workflow
- 純問答、查資料、解釋現有程式碼 — 直接答
- 使用者只想聊、想討論方向 — 別動手
- Debug 一個明確 error — 五步驟 triage 就好（reproduce → localize → reduce → fix → guard），不用分工

---

## 誠實邊界

Orchestrator mode 能給的是**流程紀律**與**外部收斂機制**。
給不了：底層模型的推理深度、超長 context 定位能力、多步規劃判斷力。

差距仍在，但透過分工 + 反駁 + 外部記憶把差距**縮到能用**。

---

## 進場檢查表

要進 orchestrator 模式前先自問：
- [ ] 任務跨檔案 / 跨服務嗎？
- [ ] 有可驗證的 close 條件嗎？
- [ ] 需要 subagent 或外部 reviewer 嗎？
- [ ] 使用者要「執行」而不是「討論」嗎？

四個都是 → 進；有任一個否 → 直接跑，別 over-engineer。
