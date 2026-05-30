---
description: Update COMMANDER_HANDOFF.md with the latest state (what's done, what's blocked, what's next)
allowed-tools: Bash, Read, Edit, Write, Glob
---

You are refreshing `COMMANDER_HANDOFF.md`. The goal: when the commander opens the repo, they see one clear page of "what I'm doing right now".

## Gather state (in parallel where possible)

1. `git log --oneline -10` — recent commits
2. `git status --short` — uncommitted (should be 0)
3. `.venv/bin/pytest tests/ -q 2>&1 | grep -E "passed|failed"` — test count
4. `make backlog` — spec backlog with run history
5. Count files: `find devswarm restaurant_api -name "*.py" | xargs wc -l | tail -1`
6. Check API key: `grep -q "ANTHROPIC_API_KEY=sk-" .env && echo SET || echo MISSING`
7. Check DB up: `pg_isready 2>/dev/null && echo UP || echo DOWN`

## Rewrite COMMANDER_HANDOFF.md with sections in this order

1. **✅ 我已完成** — pull recent commit titles, group by area
2. **🔴 你現在要做的（5 分鐘內）** — D1-D4 decisions + API key (mark which are still pending based on .env probe)
3. **🟡 你今天/明天要做的** — verification + first demo
4. **🟢 你這週要做的** — remaining specs + POS vendor talk
5. **🟢 你這個月要做的** — pilot customer + LINE OA + 電子發票
6. **📊 風險紅燈** — keep the existing table, update thresholds if needed
7. **📁 你回 repo 後一定要看的 N 個檔案** — list current docs
8. **🚨 「絕對不要」清單** — keep as is

## Don't

- Don't claim things are done that aren't (cross-check git log)
- Don't list specs that don't exist (use `ls specs/`)
- Don't change the "to-do" content unless concrete progress was made
- Don't add new sections without telling me

## When done

Output a 5-bullet diff summary: what changed in the doc vs before. Then ask if I want to `git commit` it.
