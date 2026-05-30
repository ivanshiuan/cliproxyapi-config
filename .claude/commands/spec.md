---
description: Write a new DevSwarm task spec following the project pattern
argument-hint: <module_name> [— short description of what the module should do]
allowed-tools: Read, Write, Glob
---

You are writing a new DevSwarm task brief for `specs/$ARGUMENTS.md` (or the name you parse from $ARGUMENTS).

## Required reference reading (in order, no skipping)

1. `specs/profit_calc.md` — canonical reference (best AC structure, best constraints)
2. `specs/uniform_invoice_validator.md` — second reference (simpler example)
3. `docs/04_data_schema.md` — for any column/type alignment
4. `docs/08_safety_compliance.md` — if the module touches 食安 / 勞檢 / 個資 / 發票

## Required spec sections (in order)

```markdown
# Task Brief: <Module Name>

## Background — 1-2 paragraphs. Why this exists in the Taiwan F&B context.

## Goal — 1 paragraph. Pure function, what input/output looks like.

## Scope
### In scope — bullets
### Out of scope — bullets (THIS prevents Coder over-scoping)

## Inputs (Pydantic v2)
\`\`\`python
class XxxInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    # fields with Decimal/Literal/datetime types
\`\`\`

## Output (Pydantic v2)
\`\`\`python
class XxxOutput(BaseModel):
    ...
\`\`\`

## Public Interface
\`\`\`python
def compute_xxx(input: XxxInput) -> XxxOutput:
    """One-line docstring."""
\`\`\`

## Acceptance Criteria (≥10, ≤15, each testable)

1. **AC-1 happy path**: Given <concrete numbers>, output equals <concrete numbers>.
2. **AC-2 ...**: ...
... (with worked numbers, not just shapes)

## Edge cases — bullets
## Constraints (hard requirements) — bullets
## Out of scope (be explicit so Coder doesn't drift) — bullets

## 給 PM Agent 的提醒
- ...
```

## Hard rules for the spec content

- Single .py module + single test_*.py (DevSwarm Coder is constrained to this)
- Stdlib + `pydantic>=2.5` only (no pandas/numpy)
- `Decimal` everywhere for money/qty, never float
- All ACs have **concrete worked numbers**, not just shapes
- `model_config = ConfigDict(frozen=True)` on inputs; **do not** specify `strict=True` (breaks JSON UUID parsing)
- Out-of-scope ≥3 bullets; at minimum: persistence, HTTP, multi-currency

## Length target

180-280 lines. Less means the Coder gets ambiguous; more means we're scoping too big.

## When done

1. Show me the spec content
2. Add it to `make backlog` view (it auto-detects new specs/*.md files)
3. Suggest the next step: review, then `/swarm <name>.md` to run it
