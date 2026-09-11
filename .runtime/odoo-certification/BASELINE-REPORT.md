# BASELINE-REPORT — reproducible clean baseline (Phase 2)

Environment: fresh container this session; NO pre-existing venv was reused —
`.venv` was created THIS session by `make install` (python3.12 -m venv +
`pip install -e .` from pyproject.toml, exit 0, log: raw-logs/baseline-install.log;
re-run idempotently at certification start). `pip check`: no broken requirements.
Freeze hash: raw-logs/pip-freeze.sha256. Infra from repo config: PostgreSQL 16
+ pgvector/citext/pgcrypto; migrations applied FROM ZERO on scratch DB `resto_cert`.

Gate results (pre-modification): see baseline-results.json. Summary:
ruff PASS · pyright PASS(0) · pytest 661 passed/1 skipped/1 failed
(EXTERNAL_DEPENDENCY_BLOCKED live-LINE test, predates branch, mocked
equivalent passes) · migration from-zero PASS · downgrade round-trip PASS
(procurement tables 3→0→3) · no-drift PASS · db-smoke PASS ·
repo-wide formatter: 102 legacy files unformatted = PRE_EXISTING (formatter has
never been part of the repo gate; all branch-changed files format-clean).
