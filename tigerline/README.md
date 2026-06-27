# TIGER LINE PRIME V2.0

Asian-handicap-first World Cup match decision engine.

> **Not a predictor.** A process tool. Classifies each match into one of 7
> scenarios, picks a main bet (or skips), and records the call so you can
> review it after the match.

## Quick start

```bash
# Analyze a match
.venv/bin/tiger analyze tigerline/examples/belgium_nz_two_goal.json

# Review after full-time
.venv/bin/tiger review 2026-wc-belgium-newzealand 5-1

# See backlog (matches with a plan but no review)
.venv/bin/tiger backlog

# Aggregate stats by scenario
.venv/bin/tiger stats

# Inspect / validate YAML rules
.venv/bin/tiger rules show
.venv/bin/tiger rules validate
```

## Scenarios

1. **two_goal_landing** — favorite -1.5/2 + totals 2.75+
2. **must_win_pressure** — must-win favorite at moderate line
3. **pressure_under** — final round, must-win-both, low totals
4. **goal_market** — shallow handicap + open totals → bet totals not side
5. **narrow_win** — light favorite, low totals
6. **rotation_trap** — favorite rotating despite market price — SKIP
7. **unclear_skip** — fallback when no rule fires — SKIP

## Files

- `tigerline/` — Python package (models / classifier / corridor / harness / stake / recommender / review / storage / cli)
- `tigerline/config/*.yaml` — tunable rules (edit here, not code)
- `tigerline/sql/init.sql` — SQLite schema
- `tigerline/docs/PM_SOP.md` — operating procedure (read this first)
- `tigerline/docs/ARCHITECTURE.md` — module map + design rationale
- `tigerline/examples/` — sample match inputs
- `tests/tigerline/` — pytest suite (60+ tests, all sync)

## Storage

- `~/.tigerline/tigerline.db` (override with `TIGER_DB_PATH`)
- Tables: `matches`, `classifications`, `recommendations`, `results`, `rule_updates`

## Conventions

- All money / line fields are `Decimal` — bare floats are rejected at the parser
- All Pydantic models are `frozen=True`
- All sync — no `pytest-asyncio` decorators in this package's tests

## Out of scope (V2.0)

- OCR for line screenshots
- Auto fixture fetch
- Web UI
- Parlay construction
- AI PM decision agent
- Cloud deploy
