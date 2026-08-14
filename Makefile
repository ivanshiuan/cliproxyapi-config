# ──────────────────────────────────────────────────────────────────────────
# DevSwarm + Restaurant SaaS — operational ergonomics
# Run `make help` for available targets.
# ──────────────────────────────────────────────────────────────────────────

PYTHON ?= python3.12
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

.DEFAULT_GOAL := help

# ----- meta --------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} \
	     /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo

# ----- environment -------------------------------------------------------

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

.PHONY: install
install: $(VENV)/bin/python ## Create venv and install DevSwarm in editable mode
	$(PIP) install -e .

.PHONY: clean
clean: ## Remove caches and build artifacts (keeps venv and workspace)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	rm -rf build dist *.egg-info

.PHONY: clean-all
clean-all: clean ## Also remove venv and workspace (NUKES generated agent code)
	rm -rf $(VENV) workspace/*

# ----- tests / lint ------------------------------------------------------

.PHONY: test
test: install ## Run the DevSwarm test suite (no API key required)
	$(PYTEST) tests/

.PHONY: test-fast
test-fast: ## Run tests assuming env is already set up
	$(PYTEST) tests/

.PHONY: lint
lint: install ## Lint with ruff (read-only)
	$(VENV)/bin/python -m ruff check devswarm restaurant_api tests scripts

.PHONY: typecheck
typecheck: install ## Run pyright type checker
	$(VENV)/bin/pip install --quiet pyright
	$(VENV)/bin/pyright

.PHONY: fmt
fmt: install ## Format with ruff
	$(VENV)/bin/python -m ruff format devswarm tests
	$(VENV)/bin/python -m ruff check --fix devswarm tests

# ----- swarm ops ---------------------------------------------------------

.PHONY: demo
demo: install ## Run the canonical demo task (real-profit calculator). Requires ANTHROPIC_API_KEY.
	$(PY) -m devswarm --task-file specs/profit_calc.md --verbose

.PHONY: demo-dry
demo-dry: install ## PM + Architect only on the demo task (no Coder/QA, much cheaper).
	$(PY) -m devswarm --task-file specs/profit_calc.md --dry-run --verbose

.PHONY: swarm
swarm: install ## Run a one-off task. Usage: make swarm REQ="Build me X"
	@test -n "$(REQ)" || (echo 'usage: make swarm REQ="<your task brief>"' && exit 1)
	$(PY) -m devswarm "$(REQ)" --verbose

.PHONY: backlog
backlog: ## List all DevSwarm specs and run history
	$(PY) scripts/backlog.py

.PHONY: promote
promote: install ## Promote workspace/<id>/ artifacts into restaurant_api. Usage: make promote TASK=abc12345
	@test -n "$(TASK)" || (echo 'usage: make promote TASK=<task_id>' && exit 1)
	$(PY) scripts/promote.py $(TASK)

.PHONY: backlog-json
backlog-json: ## Same as backlog, machine-readable JSON
	$(PY) scripts/backlog.py --json

# ----- restaurant backend (Phase 1) --------------------------------------

.PHONY: db-up
db-up: ## Start Postgres + Redis via docker compose
	docker compose -f restaurant_api/docker-compose.yml up -d

.PHONY: db-down
db-down: ## Stop Postgres + Redis
	docker compose -f restaurant_api/docker-compose.yml down

.PHONY: db-logs
db-logs: ## Tail Postgres logs
	docker compose -f restaurant_api/docker-compose.yml logs -f db

.PHONY: db-migrate
db-migrate: install ## Apply all pending Alembic migrations (alembic upgrade head)
	cd restaurant_api && ../$(VENV)/bin/alembic upgrade head

.PHONY: db-rollback
db-rollback: install ## Roll back the most recent migration (alembic downgrade -1)
	cd restaurant_api && ../$(VENV)/bin/alembic downgrade -1

.PHONY: db-revision
db-revision: install ## Autogenerate a new migration. Usage: make db-revision MSG="add foo"
	@test -n "$(MSG)" || (echo 'usage: make db-revision MSG="<short description>"' && exit 1)
	cd restaurant_api && ../$(VENV)/bin/alembic revision --autogenerate -m "$(MSG)"

.PHONY: db-current
db-current: install ## Show current Alembic head + DB state
	cd restaurant_api && ../$(VENV)/bin/alembic current

.PHONY: db-check
db-check: install ## Verify models match DB schema (no drift)
	cd restaurant_api && ../$(VENV)/bin/alembic check

.PHONY: db-smoke
db-smoke: install ## End-to-end insert/select smoke against real DB
	$(PY) scripts/smoke_db.py

.PHONY: db-truncate
db-truncate: ## Wipe all rows (DB owner only; bypasses ledger append-only rules)
	PGPASSWORD=$$RESTO_DB_PASSWORD psql -h $$RESTO_DB_HOST -U $$RESTO_DB_USER -d $$RESTO_DB_NAME -c \
		"TRUNCATE TABLE order_payments, order_discounts, order_lines, orders, waste_events, staff_meal_events, tasting_events, time_clocks, shifts, leave_requests, customer_points_ledger, customers, stock_movements, recipes, menu_items, menu_categories, ingredients, cash_drawer_sessions, audit_log, employees, stores, tenants CASCADE;"

.PHONY: seed
seed: install ## Seed demo restaurant data (1 tenant, 1 store, 5 employees, 12 menu items, 3 customers)
	$(PY) scripts/seed_demo_data.py

.PHONY: seed-reset
seed-reset: install ## Wipe seed tenant first, then seed fresh
	$(PY) scripts/seed_demo_data.py --reset

.PHONY: wheel-demo
wheel-demo: install ## Seed an active 開幕輪盤 campaign and print the demo URL (then `make api`)
	$(PY) scripts/seed_wheel_campaign.py

.PHONY: line-check
line-check: install ## Send a test LINE push (usage: make line-check USER=<line_user_id>)
	$(PY) scripts/line_check.py $(USER)

.PHONY: growth-demo
growth-demo: install ## Seed the full 成長飛輪 demo (儲值/裂變/UGC/RFM) and print the dashboards
	$(PY) scripts/seed_growth_demo.py

.PHONY: demo-flow
demo-flow: install ## Run the end-to-end POS day flow (打卡→開單→結帳→報廢→員工餐→下班→彙總)
	$(PY) scripts/demo_flow.py

.PHONY: full-check
full-check: install ## Run every quality gate: ruff + pyright + pytest + db-smoke + migration drift
	$(VENV)/bin/ruff check devswarm restaurant_api tests scripts
	$(VENV)/bin/pyright
	$(PYTEST) tests/
	cd restaurant_api && ../$(VENV)/bin/alembic check
	$(PY) scripts/smoke_db.py
	@echo
	@echo "✅ Full quality gate green."

.PHONY: coverage
coverage: install ## Run the suite with line + branch coverage; term-missing summary
	$(PYTEST) tests/ \
		--cov=restaurant_api --cov=devswarm \
		--cov-report=term-missing:skip-covered \
		--cov-report=html \
		--cov-branch
	@echo
	@echo "📊 HTML report: open htmlcov/index.html"

.PHONY: coverage-quick
coverage-quick: ## Coverage without re-installing or htmlgen — fastest local loop
	$(PYTEST) tests/ \
		--cov=restaurant_api --cov=devswarm \
		--cov-report=term-missing:skip-covered \
		--cov-branch -q

.PHONY: api
api: install ## Run the FastAPI restaurant backend in dev mode (auto-reload)
	$(VENV)/bin/uvicorn restaurant_api.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: jobs
jobs: install ## Run the background-job scheduler (expiry warning, points expire, COGS variance)
	$(PY) -m restaurant_api.jobs

.PHONY: jobs-once
jobs-once: install ## Run all nightly jobs once and exit (for cron-style external schedulers)
	$(PY) -c "import asyncio; from restaurant_api.jobs import run_expiry_warning, run_points_expire, run_campaign_voucher_expiry, run_cogs_variance_check; \
		asyncio.run(run_expiry_warning()); asyncio.run(run_points_expire()); asyncio.run(run_campaign_voucher_expiry()); asyncio.run(run_cogs_variance_check())"

.PHONY: openapi
openapi: install ## Export OpenAPI 3 schema to openapi.json (regenerate any time)
	$(PY) scripts/export_openapi.py

# ----- Odoo staging deployment (idempotent one-command automation) -------

.PHONY: deploy-staging
deploy-staging: install ## Staging 部署 PLAN (唯讀, 印出會做什麼; 這是唯一你要記的指令)
	$(PY) scripts/deploy_staging.py plan

.PHONY: deploy-staging-apply
deploy-staging-apply: install ## Converge staging to desired state (idempotent, run on staging host)
	$(PY) scripts/deploy_staging.py apply

.PHONY: deploy-staging-verify
deploy-staging-verify: install ## Run the 46-item acceptance checklist -> evidence JSON + SHA256
	$(PY) scripts/deploy_staging.py verify

.PHONY: migration-safety
migration-safety: ## Scan Alembic migrations for unsafe ops (DROP COLUMN, NOT NULL, ...)
	$(PY) scripts/check_migration_safety.py

.PHONY: migration-safety-strict
migration-safety-strict: ## As above but exits 1 on MEDIUM findings too (use in CI)
	$(PY) scripts/check_migration_safety.py --strict

# ----- pre-commit hooks --------------------------------------------------

.PHONY: install-hooks
install-hooks: install ## Install pre-commit hooks (ruff + secrets/env guard + pyright)
	$(VENV)/bin/pip install --quiet pre-commit
	$(VENV)/bin/pre-commit install

.PHONY: hooks-run
hooks-run: install ## Run all pre-commit hooks against every tracked file
	$(VENV)/bin/pre-commit run --all-files

# ----- production docker -------------------------------------------------

.PHONY: docker-build
docker-build: ## Build the production image (usage: make docker-build TAG=v0.1.0)
	docker build -t resto-api:$${TAG:-latest} -f restaurant_api/Dockerfile .

.PHONY: docker-prod-up
docker-prod-up: ## Bring up production-shape stack (requires .env.production)
	@test -f .env.production || (echo "Missing .env.production at repo root" && exit 1)
	docker compose -f restaurant_api/docker-compose.production.yml up -d

.PHONY: docker-prod-down
docker-prod-down: ## Tear down production-shape stack
	docker compose -f restaurant_api/docker-compose.production.yml down

# ----- file ingest -------------------------------------------------------

.PHONY: to-md
to-md: ## Convert a file to Markdown for token-cheap reading (usage: make to-md FILE=x.pdf [OUT=y.md])
	@test -n "$(FILE)" || (echo "usage: make to-md FILE=<path> [OUT=<path>]" && exit 1)
	uv run scripts/to_md.py "$(FILE)" $(if $(OUT),-o "$(OUT)",)

# ----- info --------------------------------------------------------------

.PHONY: status
status: ## Print repo status: branch, commits, last-modified files
	@git rev-parse --abbrev-ref HEAD | xargs -I {} echo "branch: {}"
	@git log --oneline -5
	@echo
	@echo "tracked files: $$(git ls-files | wc -l)"
	@echo "untracked:     $$(git ls-files --others --exclude-standard | wc -l)"
