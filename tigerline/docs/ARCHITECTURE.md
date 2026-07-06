# TIGER LINE PRIME — Architecture

## Pipeline

```
MatchInput (JSON)
   │
   ▼
parser.parse_match()        Pydantic v2 frozen validation
   │
   ▼
classifier.classify()       7-scenario YAML decision tree
   │
   ├──▶ corridor.build_corridor()   scenario → score corridor (away-flip aware)
   │
   ├──▶ harness.evaluate()           team_need + risk_flags → upgrade/normal/downgrade/skip
   │
   ├──▶ stake.pick_level()           confidence + adjustment → A+/A/B/C/D
   │     stake.allocate()            level + bankroll → (pct, amount)
   │
   ▼
recommender.recommend()     compose BetPlan: main + secondary + CS + avoid + reserve_live
   │
   ▼
storage.save_*              SQLite persistence (idempotent on match_id)
```

## File map

| File | Purpose |
|------|---------|
| `models.py` | All Pydantic v2 schemas. Single source of truth for `ScenarioType`, `StakeLevel`, `MainResult`. |
| `parser.py` | JSON → `MatchInput`. Thin wrapper. |
| `rules.py` | YAML loaders with `lru_cache`. Validates rule shape on load. |
| `classifier.py` | Walks `classification_rules.yaml` in order, first hit wins. |
| `corridor.py` | Looks up template from `corridor_templates.yaml`, flips if away is favorite. |
| `harness.py` | Pure logic — no rule file. Reads scenario + confidence + risk flags. |
| `stake.py` | Maps confidence → level, level + adjustment → percentage. |
| `recommender.py` | Composes everything into `BetPlan`. Holds scenario-to-market mapping. |
| `review.py` | Post-match settlement + 0-100 scorecard + rule-update suggestions. |
| `storage.py` | sqlite3 stdlib CRUD. No SQLAlchemy. |
| `cli.py` | Typer app with subcommands. |
| `config/*.yaml` | Tunable rules. Edit here, not in code. |
| `sql/init.sql` | One-shot schema. |

## Design choices

### Why YAML for rules?

The classifier's decision tree was the highest-risk code surface — every line of
Python in the rule walk would be a place to introduce a bug. By keeping the
**rules** in YAML and the **walk** in code, the rule edits cannot break the walk
and the walk's shape is fixed forever.

### Why sqlite3 stdlib, not SQLAlchemy?

This is a single-writer CLI. There are no relations beyond FK-by-string, no
async concern, no migration burden beyond the one DDL. SQLAlchemy would add
machinery without lifting weight. The repo's SQLAlchemy stack is reserved for
the Postgres restaurant_api — segregation is a feature.

### Why no `strict=True` on Pydantic models?

`strict=True` would block string → Decimal coercion (`"-1.75"` reaches the
parser as a string in JSON). We need `BeforeValidator(_reject_float)` because
the danger isn't strings, it's bare floats — `-1.75` as a JSON number coerces
into a Decimal with binary rounding error.

### Shared data layer with SID

`/data/matches/<match_id>.json` holds the **canonical** match identity (5
fields). Each system (TIGER, SID) maintains its own overlay at
`<match_id>.tiger.json` / `<match_id>.sid.json`. The two systems agree on the
ID and the canonical meta; everything else is local. This avoids the "shared
schema becomes a coupling tax" trap.

## What's NOT here

- OCR for line screenshots — V2.1
- Auto fixture fetch — V2.1
- Web UI — V2.1+
- AI PM decision agent — V2.2 (needs 50+ reviewed matches first)
- Cloud deploy — pure local CLI
