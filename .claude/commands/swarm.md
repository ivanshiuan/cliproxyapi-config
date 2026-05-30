---
description: Run a DevSwarm task safely with budget guard, then promote the artifact if tests pass
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

3. **Budget**: Always pass `--budget 5.0` unless I explicitly override

4. **Confirm**: Show me the spec name + estimated cost (USD < $5) + max heal iterations (5). Wait for "go".

## Execute

```bash
.venv/bin/python -m devswarm <args> --budget 5.0 --verbose
```

## Post-run

After exit:
- If exit code 0 and artifacts written: offer `make promote TASK=<task_id>` and ask if I want it promoted.
- If exit code 1 and budget halted: report final cost + suggest tightening the spec.
- If exit code 1 and heal exhausted: print the last `qa_report` and let me decide.
- If exit code 3 (graph error): print the exception, don't promote.

Never silently promote — always ask first since promotion stages files into `restaurant_api/services/`.
