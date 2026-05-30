---
description: Run every quality gate (ruff + pyright + pytest + alembic-check + db-smoke) and report a one-line verdict
allowed-tools: Bash, Read
---

You are running the project's full quality gate. Execute these in sequence and surface failures clearly:

1. `sudo service postgresql start 2>/dev/null || true` — make sure DB is up
2. `.venv/bin/ruff check devswarm restaurant_api tests scripts`
3. `.venv/bin/pyright`
4. `.venv/bin/pytest tests/ -q`
5. `cd restaurant_api && ../.venv/bin/alembic check`
6. `.venv/bin/python scripts/smoke_db.py`

If pytest fails because of stale DB rows, suggest `make db-truncate` and ask before running it (destructive).

After all 5 pass, output exactly one summary line in this format:

```
✅ all green — <test_count> tests, ruff clean, pyright 0, alembic no-drift, db smoke ok
```

If anything fails, output:

```
❌ <gate_name> failed — <one-sentence diagnosis>
```

Then offer the next step (fix the lint? rerun test? truncate DB?). Don't auto-fix lint without asking unless errors are purely `I001` / `UP` (import sort / pep-upgrade — safe).
