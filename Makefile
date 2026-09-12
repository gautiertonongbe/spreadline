.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
# A file-backed SQLite database, so the platform runs with no database server.
LOCAL_DB := sqlite+pysqlite:///$(CURDIR)/$(BACKEND)/spreadline.db

export PYTHONPATH := $(CURDIR)/$(BACKEND)

.PHONY: help
help: ## Show the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Create the virtualenv and install backend and frontend dependencies
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	cd frontend && npm install --no-audit --no-fund

.PHONY: migrate
migrate: ## Apply database migrations
	cd $(BACKEND) && DATABASE_URL="$(LOCAL_DB)" .venv/bin/alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="what changed"
	cd $(BACKEND) && DATABASE_URL="$(LOCAL_DB)" .venv/bin/alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: migrate ## Populate the database by analysing the whole fixture catalogue
	cd $(BACKEND) && DATABASE_URL="$(LOCAL_DB)" .venv/bin/python -m scripts.seed

.PHONY: reset
reset: ## Drop the local database and rebuild it from fixtures
	rm -f $(BACKEND)/spreadline.db
	$(MAKE) seed

.PHONY: api
api: ## Run the API on :8000
	cd $(BACKEND) && DATABASE_URL="$(LOCAL_DB)" \
		CORS_ORIGINS="http://localhost:3000" \
		.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

.PHONY: web
web: ## Run the frontend on :3000
	cd frontend && npm run dev

.PHONY: test
test: ## Run the backend test suite
	cd $(BACKEND) && .venv/bin/python -m pytest -q

.PHONY: lint
lint: ## Lint and typecheck both sides
	cd $(BACKEND) && .venv/bin/ruff check app tests scripts migrations
	cd frontend && npx tsc --noEmit

.PHONY: format
format: ## Format the backend
	cd $(BACKEND) && .venv/bin/ruff format app tests scripts migrations
	cd $(BACKEND) && .venv/bin/ruff check --fix app tests scripts migrations

.PHONY: check
check: lint test ## Everything CI would run

.PHONY: up
up: ## Start the full stack in Docker
	docker compose up --build

.PHONY: down
down: ## Stop the Docker stack
	docker compose down

.PHONY: clean
clean: ## Remove build artefacts and caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache frontend/.next
