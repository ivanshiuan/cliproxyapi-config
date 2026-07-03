#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Claude Code + orchestrator workflow aliases
# ─────────────────────────────────────────────────────────────
# 使用方式（一次設好）：
#   cat scripts/aliases.sh >> ~/.zshrc     # 或 ~/.bashrc
#   source ~/.zshrc                        # 或 source ~/.bashrc
#
# 之後就有：cc / copus / corch / crev / cdomain / cswarm 可用
# 完整策略見：docs/19_operating_playbook.md
# ─────────────────────────────────────────────────────────────

# 快速隨手 — 預設模型、跳權限（小事 YOLO）
alias cc='claude --dangerously-skip-permissions'

# Opus 4.8 認真模式 — 帶行為紀律 scaffold（9 條）+ 跳權限
# 用途：需要判斷、需要收尾自檢、需要平行 tool call 的任務
alias copus='claude --model claude-opus-4-8 --output-style opus-harness --dangerously-skip-permissions'

# Orchestrator 模式 — Opus 4.8 + harness + 自動附加 orchestrator 契約
# 用途：跨檔案 / 跨服務 / 需驗收 / 需 close 的大任務
alias corch='claude --model claude-opus-4-8 --output-style opus-harness --dangerously-skip-permissions --append-system-prompt "$(cat ~/.claude/skills/orchestrator-mode/SKILL.md 2>/dev/null || cat .claude/skills/orchestrator-mode/SKILL.md 2>/dev/null || echo)"'

# Review 模式 — 用 Sonnet + code-review skill（省 token 的 reviewer）
# 用途：orchestrator 派 review 用（比 Opus 便宜、review 不需推理深度）
alias crev='claude --model claude-sonnet-4-6 --dangerously-skip-permissions'

# Domain expert 模式 — 直接跳 restaurant-domain-expert agent
# 用途：Taiwan F&B 規範問題、發票、勞基法、POS
alias cdomain='claude --dangerously-skip-permissions --append-system-prompt "$(cat .claude/agents/restaurant-domain-expert.md 2>/dev/null | sed \"1,/^---$/d;/^---$/,/^---$/d\")"'

# DevSwarm 蜂群 — 走 Makefile，不直接叫 claude
# 用途：Phase 0 型 spec 任務（單 module + test）
alias cswarm='make swarm REQ="$(cat "$1")"'

# ─────────────────────────────────────────────────────────────
# 保護欄（避免誤打）
# ─────────────────────────────────────────────────────────────

# `cyolo` 是 `cc` 的別名 — 提醒你這是 YOLO
alias cyolo='cc'

# ─────────────────────────────────────────────────────────────
# 情境快查（打 `ccquick` 看一頁參考）
# ─────────────────────────────────────────────────────────────

ccquick() {
  cat <<'EOF'
Claude Code 分流表（quick reference）

小事 / typo / rename       → cc
Debug / test 紅            → cc + Addy debugging-and-error-recovery 五步驟
新 spec                    → cc → spec-writer agent
新 router                  → cc → router-implementer agent（可平行多個）
DevSwarm 蜂群              → cswarm specs/xxx.md
跨檔案大任務               → corch（進 orchestrator mode）
Taiwan F&B 規範            → cdomain
Review 現有 PR / diff      → crev + /code-review skill
部署 SID                   → cc + 說「/deploy-sid」
部署 restaurant_api        → cc + 說「/deploy restaurant_api」+ 走 docs/11

永遠：
- commit 前 make full-check
- 收工 /handoff 更新 HANDOFF
- 跨 session 記憶靠 docs/ 不靠腦袋

完整策略：docs/19_operating_playbook.md
EOF
}

# ─────────────────────────────────────────────────────────────
# RTK（若已裝）
# ─────────────────────────────────────────────────────────────
export RTK_TELEMETRY_DISABLED=1
# rtk 完整安裝步驟見 docs/18_competitor_watch.md § B.1

# ─────────────────────────────────────────────────────────────
# 補完提示
# ─────────────────────────────────────────────────────────────
if command -v claude >/dev/null 2>&1; then
  # 只在有 claude CLI 時印 tip
  :  # 保留位置，未來想加提示訊息
fi
