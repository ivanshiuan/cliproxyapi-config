# TIGER LINE PRIME — PM SOP

> **Audience**: Ivan. Read before every analysis session.
> **Scope**: MVP (V2.0). No OCR, no auto-fetch, no front-end.

## §1 What this system is

A deterministic decision engine for Asian-handicap soccer markets. It classifies
each match into one of seven scenarios, produces a score corridor, picks a main
bet (or skips), and records the call so you can review it after the match.

It is **not** a predictor. It is a process tool. Win/loss is not the success
metric — process consistency is.

## §2 Pre-match input checklist (run before `tiger analyze`)

For every `match.json` in `data/matches/`:

- [ ] `match_id` is kebab-case ASCII (e.g. `2026-wc-belgium-newzealand`)
- [ ] `kickoff_utc` is ISO-8601 with timezone offset
- [ ] `home` / `away` are full team names (Belgium, not BEL)
- [ ] `group_context.final_round` is set true/false (not omitted)
- [ ] `group_context.same_time_kickoff` is set if final round has parallel kickoffs
- [ ] `team_need.home` is one of `must_win | draw_ok | rotation | neutral | unknown`
- [ ] `team_need.away` likewise
- [ ] `market.line` is a string (`"-1.75"`, not the JSON number -1.75)
- [ ] `market.favorite` is `home` or `away`
- [ ] `totals.line` is a string
- [ ] `bankroll` is a string (`"5000"`, not 5000)
- [ ] `risk_flags` lists any of: `favorite_already_qualified`, `key_player_out`,
      `parallel_kickoff_risk`, `final_round_pressure`, `weather_risk`

If anything is missing, **do not** run `tiger analyze` — fix the JSON first.

## §3 Running the analysis

```
.venv/bin/tiger analyze data/matches/<match_id>.tiger.json
```

The engine prints:
- Scenario + confidence
- Main bet (or SKIP)
- Secondary legs
- Correct-score sprinkles
- Avoid list
- Reserve-for-live notes

It also persists everything to SQLite at `~/.tigerline/tigerline.db`.

## §4 Reading the output

- **SKIP in red** → walk away. Do not bet "just in case."
- **A+ green** → max conviction (18-25% bankroll). Don't size up beyond the band.
- **B yellow** → moderate. Halve if your overall day's exposure is already heavy.
- **C dim** → small. These are sprinkles. They are not a portfolio.
- **D red** → engine says don't bet. Listen.

## §5 Post-match review checklist

Within 6 hours of full-time, run:

```
.venv/bin/tiger review <match_id> <H>-<A>
```

The engine returns:

1. **Scenario correct?** — did the rule still fire after the result?
2. **Corridor hit?** — was the score in the 3-5 candidate set?
3. **Main result** — `win` / `half_win` / `push` / `half_lose` / `lose` / `skipped`
4. **0-100 score** — 40 scenario + 30 corridor + 30 main
5. **Suggestions** — pending rule tweaks (queued in `rule_updates`)

Ask yourself:
- If the scenario held but the corridor missed, the corridor template is too
  narrow. Note it; do NOT edit YAML on the spot.
- If the scenario broke (re-classification flipped), the rule order is wrong.
  Note it.
- Apply YAML changes in a single Sunday review session, not match-by-match.

## §6 Weekly review (Sunday)

```
.venv/bin/tiger stats --since <last-sunday>
```

Look at scenarios with `n >= 5`. If `main_win < 50%`:
- The rule fires too aggressively → tighten its `when` clause
- Or the main-bet selection is wrong → revisit `_main_selection` in
  `recommender.py`

Edit `tigerline/config/*.yaml`. Run `tiger rules validate` before saving.
Run the canonical fixture tests to confirm Belgium/Egypt/rotation cases
still classify correctly.

## §7 When to escalate

- 10+ matches in a row where main bet loses with `corridor_hit=True` → something
  systematic is wrong with the main-bet picker. Stop betting; dig into the code.
- 3+ matches where the engine SKIPped and the underdog won outright at long odds
  → the trap rule is working as intended. Keep skipping.

## §8 What this SOP does NOT cover

- Live (in-play) betting — `reserve_live` is a manual reminder, not automated
- Parlay construction — engine never auto-parlays
- Cross-platform line shopping — single-platform input only
- Bankroll management beyond per-match sizing — track aggregate exposure
  outside the tool
