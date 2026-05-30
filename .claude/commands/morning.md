---
description: Morning routine — wake up the project (start DB, check tests, show backlog, surface blockers)
allowed-tools: Bash, Read
---

Run the morning startup ritual for this project. Output sections in this exact order:

## 1. Wake up infrastructure

```bash
sudo service postgresql start 2>&1 | tail -1
sleep 1
pg_isready
```

## 2. Sanity check (must all green)

```bash
.venv/bin/pytest tests/ -q 2>&1 | tail -3
.venv/bin/ruff check devswarm restaurant_api tests scripts 2>&1 | tail -2
cd restaurant_api && ../.venv/bin/alembic check 2>&1 | tail -2 && cd ..
```

## 3. Status snapshot

```bash
git log --oneline -5
git status --short
```

## 4. Backlog

```bash
make backlog
```

## 5. Blockers — explicit checks

- `.env` has `ANTHROPIC_API_KEY=sk-...` ? → if no, **highlight in red**
- D1-D4 decisions in `COMMANDER_HANDOFF.md` ? → if any blank, list which ones
- Any uncommitted changes? → if yes, ask whether to commit before doing anything else

## 6. Recommendation

In ≤3 sentences, tell the commander what the highest-ROI next move is right now. Pick from:
- "Fill API key + run `make demo`" (if key missing)
- "Pick a spec to run with `/swarm <name>`" (if key OK + specs pending)
- "Wire up the next router" (if all specs done)
- "Look at the open question in COMMANDER_HANDOFF.md" (if a decision is overdue)
- "All green, no obvious next move — go open the store" (if everything truly done)

Don't ramble. One recommendation. End with a single concrete command.
