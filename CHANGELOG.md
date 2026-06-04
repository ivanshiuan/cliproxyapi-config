# Changelog

All notable changes to this project. Format follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

---

## [Unreleased] — Production Hardening

Production-readiness pass — the "Day 1 we open the real store" milestone.
Adds observability, K8s-style health, background jobs, pre-commit gates,
production deploy package.

### Added
- **Observability layer** (`restaurant_api/middleware/`):
  - `RequestContextMiddleware` — stamps every request with X-Request-Id
    (echoed via header), exposes it via contextvar.
  - `JSONFormatter` + `configure_logging()` — structured JSON logs with
    automatic request_id / tenant_id correlation and secret-key redaction.
- **K8s health endpoint split** (`restaurant_api/api/health.py`):
  - `GET /health/live` — liveness probe (process alive). Returns 503 when
    draining so LBs detach cleanly on graceful shutdown.
  - `GET /health/ready` — readiness probe (DB + deps reachable). 503 on
    any check failure.
  - `GET /health` — backward-compat composite, routes to `/health/ready`.
- **Audit logging service** (`restaurant_api/services/audit_service.py`):
  - `audit()` one-call API that writes append-only `audit_log` rows.
  - Auto-pulls request_id / tenant_id from context.
- **Background jobs framework** (`restaurant_api/jobs/`):
  - APScheduler in-process with proper SIGTERM handling.
  - `expiry_warning` (03:00 daily) — flags ingredients within 3 days of
    expiry that still have on-hand stock.
  - `points_expire` (03:30 daily) — writes reversing ledger rows for
    expired customer points.
  - `cogs_variance_check` (04:00 daily) — alerts when actual COGS vs
    theoretical drifts > 5% of net revenue per store/day.
  - All idempotent within the calendar day; failures logged but don't
    crash the scheduler.
- **Production deploy package**:
  - `restaurant_api/Dockerfile` — multi-stage (builder/runtime), non-root
    user (uid 10001), tini PID-1, container HEALTHCHECK.
  - `restaurant_api/docker-compose.production.yml` — full stack
    (db+redis+migrate+api+jobs) with proper depends_on conditions, memory
    limits, log rotation.
  - `docs/11_production_deployment.md` — single-VM deploy SOP, T-7
    open-store checklist, DR runbook.
- **Pre-commit hooks** (`.pre-commit-config.yaml`):
  - ruff format + lint, basic file checks, project-local guards blocking
    `.env*` and `workspace/` from accidental commit.
  - pyright basic mode gate.
- **Tests**: middleware (8), health endpoints (5), audit service (3),
  background jobs (4) = +20 tests.

### Changed
- `restaurant_api/main.py` now installs `RequestContextMiddleware`,
  configures structured logging on lifespan startup, and calls
  `health.mark_shutting_down()` on shutdown for graceful drain.
- The old monolithic `GET /health` handler in `main.py` removed in favour
  of the dedicated `api/health.py` router.

### Required Migrations
None — these changes are app-layer only.

### Operational Notes
- Run jobs alongside the API in production: `python -m restaurant_api.jobs`
- Single-instance scheduler — do not run multiple replicas of the jobs
  container.
- Set `RESTO_LOG_LEVEL=INFO` for prod; structured JSON output is
  Loki/Cloudwatch/ELK ready.

---

## [0.1.0] — 2026-05-29 — Phase 1 MVP Foundation

Initial commit through Phase 1 (極窄 MVP). 17 commits.

### Added
- **DevSwarm AI agent shop** (`devswarm/`):
  - 4-agent LangGraph swarm (PM → Architect → Coder → QA) with
    self-heal loop, max-heal exhaustion, budget guard.
  - Anthropic SDK wrapper with prompt caching, tool-use loop, retry.
  - Workspace sandbox with path-traversal guard + pytest subprocess
    runner with rlimit + 60s timeout.
  - CLI with --verbose streaming, --dry-run, --max-heal, --budget USD N.
  - Rich-rendered live telemetry; cost accounting.
  - 4-model selection: Opus 4.7 (PM/Architect), Sonnet 4.6 (Coder),
    Haiku 4.5 (QA).

- **Restaurant API backend** (`restaurant_api/`):
  - 25 tables across 11 model files: tenants, stores, employees,
    menu (categories + items + allergens JSONB), inventory (ingredients
    + recipes + append-only stock_movements with lot_no), orders
    (header + lines + discounts + payments + 統一發票 lifecycle),
    cost events (waste/staff_meal/tasting), HR (shifts + time_clocks
    with 4 labor-law buckets + leave_requests), reservations + walk-in
    queue, cash_drawer_sessions, audit_log, customers + points_ledger,
    embeddings (pgvector 1536-dim).
  - 4 FastAPI routers (orders/stock/clock/events) with 11 endpoints,
    37 router integration tests.
  - 3 Alembic migrations applied; pgvector + uuid-ossp + citext +
    pg_trgm extensions enabled.
  - DB-level INSTEAD NOTHING rules enforce append-only on three ledger
    tables (stock_movements, audit_log, customer_points_ledger).
  - LINE integration layer with Stub (Phase 1) + HttpLineMessenger
    skeleton (Phase 2 wiring).
  - Commit-at-DI-layer pattern (services flush, only get_db commits).
  - Pydantic v2 with `frozen=True` + `StrictDecimal` BeforeValidator
    (rejects float literals while still accepting JSON UUID strings).

- **Specs / DevSwarm task briefs** (10 in `specs/`):
  - 6 calc-engine modules: profit_calc, bom_consumer, discount_resolver,
    cogs_variance_detector, labor_hours_classifier,
    uniform_invoice_validator (台灣統編 checksum).
  - 4 router contracts: orders, stock, clock, events.

- **Strategy docs** (10 in `docs/` + CLAUDE.md + COMMANDER_HANDOFF.md):
  - 00 vision freeze (SSOT for the 7-module restaurant SaaS).
  - 01 tech stack rec (FastAPI + Next.js + Postgres + pgvector).
  - 02 DevSwarm architecture manual.
  - 03 roadmap (Phase 0 → 5, ~470 萬 cumulative cost).
  - 04 909-line schema doc including mv_daily_pnl materialized view.
  - 06 execution plan with 12 tasks + 4 commander decisions.
  - 07 DevSwarm runbook (first `make demo` troubleshooting).
  - 08 食安/勞檢/個資/災難 SOP runbook.
  - 09 Phase 1 extension kit (KDS / reservation / LINE designs).
  - 10 Claude Code workflow (when to use which power feature).

- **Claude Code workspace setup** (`.claude/`):
  - CLAUDE.md project memory.
  - 5 custom slash commands: `/check /swarm /spec /handoff /morning`.
  - 3 formal subagents: spec-writer, router-implementer,
    restaurant-domain-expert.
  - settings.json with 43 auto-allow / 10 ask-gate permissions + 3 hooks
    (PostToolUse auto-format, SessionStart PG+API-key probe, PreCompact
    audit).

- **CI + tooling**:
  - GitHub Actions workflow with real Postgres + pgvector service,
    alembic check, pytest, ruff, pyright.
  - Makefile with 27 targets across help/test/lint/typecheck/demo/swarm/
    backlog/promote/db-{up,down,migrate,smoke,truncate,…}/api.
  - scripts/: backlog, promote (with double-gate pytest), seed_demo_data,
    demo_flow (end-to-end 7-step POS day), smoke_db.

### Verified
- 106/106 pytest green (mock + real-DB integration mix).
- ruff clean across devswarm/restaurant_api/tests/scripts.
- pyright 0 errors, 0 warnings.
- Alembic `alembic check` zero drift.
- End-to-end demo flow stamps real rows: 1 order → 2 lines → 7 stock
  moves → 1 waste → 1 staff meal → 1 clock cycle.
- Bug caught + fixed during demo: services were calling `flush()` only
  (never `commit()`). Fixed at DI layer (`api/deps.py::get_db` now
  commits on success, rolls back on exception). This would have been a
  P0 production data-loss bug.
