---
description: Run a DevSwarm spec against opus/sonnet/haiku in parallel, then recommend the cheapest model that passes
argument-hint: <specs/foo.md> [opus,sonnet]
allowed-tools: Bash, Read
---

You are running a model bake-off. Args: $ARGUMENTS

**What this does**: stolen from OpenAI's `gpt-5-coding-examples` repo, where each
example has a `-5.2` sibling using the same prompt — that side-by-side is the
cheapest signal of "is the smaller model good enough?". For DevSwarm, we vary
only the **Coder** model; PM/Architect/QA defaults stay constant.

## Pre-flight

1. **Parse args**:
   - First arg ending in `.md` and existing under `specs/` → the spec path
   - Optional second arg = comma-separated models (default `opus,sonnet,haiku`)
   - If no spec found → stop and ask me which one

2. **API key check**: `grep -q "ANTHROPIC_API_KEY=sk-" .env || echo MISSING`
   If MISSING → stop and tell me to fill `.env`.

3. **Spec validation**: bakeoff.py runs this itself, but show me the spec's
   `kind`, `ac_count`, `preferred_model`, `budget_usd` first so I know what's about
   to be run.

4. **Cost estimate**: each model run uses ~$0.30-$5.00 budget. Three models ≈
   $1-15 worst case. Confirm with me before launching.

## Execute

```bash
.venv/bin/python scripts/bakeoff.py <spec> --models <models>
```

Runs in parallel (default concurrency = min(3, # models)). Streams per-model
results as each finishes. Wall-clock = slowest run, not sum.

## Post-run

Read `workspace/bakeoff/<spec_id>/bakeoff.md` and surface to me:

1. **Which models passed** (✅) and which didn't (❌)
2. **Cheapest passing model** + the % savings vs Opus
3. **Recommended action**:
   - If a cheaper model passed → "update `preferred_model:` in `specs/<id>.md` to `<cheaper>`"
     and offer to do the edit
   - If only Opus passed → "spec needs Opus; keep current setting"
   - If none passed → "spec likely under-specified; surface the QA root cause"

For each failing run, point me at the workspace path so I can inspect.

## Don't

- Don't auto-update the spec's `preferred_model` without confirming with me.
- Don't delete bakeoff workspaces — they're the audit trail.
- Don't run more than one bakeoff at once (concurrency × 3 models × budget can blow through monthly cap fast).
