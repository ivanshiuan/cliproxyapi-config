---
description: Write a new DevSwarm task spec (structured YAML frontmatter + 4-section body, gated by validate_spec.py)
argument-hint: <module_name> [— short description of what the module should do]
allowed-tools: Read, Write, Glob, Bash
---

You are writing a new DevSwarm task brief for `specs/$ARGUMENTS.md` (or the name you parse from $ARGUMENTS).

## Required reference reading (in order, no skipping)

1. `specs/profit_calc.md` — canonical **pure-function** spec (15 ACs, full frontmatter)
2. `specs/orders_router.md` — canonical **router** spec
3. `scripts/validate_spec.py` — the gate your spec must pass before this command returns
4. `docs/04_data_schema.md` — for any column/type alignment
5. `docs/08_safety_compliance.md` — if the module touches 食安 / 勞檢 / 個資 / 發票

## Mandatory frontmatter

Every spec MUST start with this YAML block (between `---` fences):

```yaml
---
id: <filename_stem>          # matches the .md filename
title: <Human Title>
module: <snake_case>         # the .py file Coder produces
kind: pure-function          # OR `router`
status: draft                # draft|ready|implemented|deprecated
preferred_model: sonnet      # opus|sonnet|haiku
budget_usd: 5.0
tags: [domain, mvp, pure-function]
ac_count: 12                 # # of unique AC-N markers in body
---
```

## Body sections (kind-specific)

**kind: pure-function** → `Background → Goal → Scope (In/Out) → Inputs → Output → Public Interface → Acceptance Criteria → Edge cases → Constraints`

**kind: router** → `Background → Routes → Pydantic Schemas → Database writes → Acceptance Criteria → Error responses → Out of scope`

## Hard rules

- Body 100-400 lines (hard); target 150-280
- ≥10 ACs with concrete worked numbers, not just shapes
- Pydantic v2 `frozen=True`, NEVER `strict=True`
- All money/qty as `Decimal`
- Single .py + single test_*.py (Coder is single-file by design)

## Mandatory post-write step

```bash
python3.12 scripts/validate_spec.py specs/<id>.md
```

If exit code != 0, fix the spec and re-run. Do not finish until it's green.
If `ac_count` warns of mismatch, run:

```bash
python3.12 scripts/validate_spec.py --fix-counts specs/<id>.md
```

## When done — show me

1. Validator output (must be ✅)
2. Frontmatter block
3. AC count + body line count
4. Suggested next step: `/swarm <id>.md` to run it, or `/bakeoff specs/<id>.md` to compare model tiers first
