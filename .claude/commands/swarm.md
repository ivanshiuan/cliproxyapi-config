---
description: Run a DevSwarm task safely — validates the spec first, runs with budget guard, offers to promote on green
argument-hint: <spec_filename_or_inline_request>
allowed-tools: Bash, Read, Edit, Write
---

You are launching a DevSwarm task. Args: $ARGUMENTS

## Pre-flight (must complete before invoking DevSwarm)

1. **API key check**: `grep -q "ANTHROPIC_API_KEY=sk-" .env || echo MISSING`
   If MISSING → stop and tell me to fill `.env`. Don't proceed.

2. **Argument resolution**:
   - If `$ARGUMENTS` ends in `.md` and exists in `specs/` → treat as `--task-file`
   - Else → treat as inline request

3. **Spec validation** (only when running from a spec file):
   ```bash
   python3.12 scripts/validate_spec.py specs/<id>.md
   ```
   If exit code != 0 → stop and tell me what's wrong with the spec. Do NOT proceed
   — running an underspec spec just burns Coder budget for nothing. The fix is
   to update the spec, not bypass the check.

4. **Budget**: Read `budget_usd` from the spec's frontmatter. Use that as `--budget`.
   For inline requests, default to `--budget 5.0` unless I override.

5. **Model selection**: Read `preferred_model` from frontmatter. Map:
   - `opus` → `DEVSWARM_MODEL_CODER=claude-opus-4-7`
   - `sonnet` → `DEVSWARM_MODEL_CODER=claude-sonnet-4-6` (default, can omit)
   - `haiku` → `DEVSWARM_MODEL_CODER=claude-haiku-4-5-20251001`

6. **Confirm**: Show me {spec id, kind, ACs, preferred_model, budget}. Wait for "go".

## Execute

```bash
DEVSWARM_MODEL_CODER=<resolved> .venv/bin/python -m devswarm <args> --budget <from spec> --verbose
```

## Post-run

After exit:
- Exit 0 + artifacts written: offer `make promote TASK=<task_id>` and ask if I want it promoted.
- Exit 1 + budget halted: report final cost + suggest tightening the spec (point at the failed AC if known).
  Mention `/bakeoff specs/<id>.md` as an alternative — maybe Opus is overkill for this spec.
- Exit 1 + heal exhausted: print the last `qa_report` and let me decide.
- Exit 3 (graph error): print the exception, don't promote.

Never silently promote — always ask first since promotion stages files into `restaurant_api/services/`.
