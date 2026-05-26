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
	$(VENV)/bin/python -m ruff check devswarm tests

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

.PHONY: api
api: install ## Run the FastAPI restaurant backend in dev mode (auto-reload)
	$(VENV)/bin/uvicorn restaurant_api.main:app --reload --host 0.0.0.0 --port 8000

# ----- info --------------------------------------------------------------

.PHONY: status
status: ## Print repo status: branch, commits, last-modified files
	@git rev-parse --abbrev-ref HEAD | xargs -I {} echo "branch: {}"
	@git log --oneline -5
	@echo
	@echo "tracked files: $$(git ls-files | wc -l)"
	@echo "untracked:     $$(git ls-files --others --exclude-standard | wc -l)"
