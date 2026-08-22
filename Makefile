.DEFAULT_GOAL := help
SHELL := /bin/bash
API := apps/api
WEB := apps/web

.PHONY: help setup up down logs psql redis api web dev test lint fmt typecheck check clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies and create .env
	@test -f .env || cp .env.example .env
	cd $(API) && uv sync --all-groups
	cd $(WEB) && pnpm install

up: ## Start Postgres + Redis
	docker compose up -d

down: ## Stop Postgres + Redis
	docker compose down

logs: ## Tail datastore logs
	docker compose logs -f

psql: ## Open a psql shell
	docker compose exec postgres psql -U chessmark -d chessmark

redis: ## Open a redis-cli shell
	docker compose exec redis redis-cli

api: ## Run the API with reload (port 8010)
	cd $(API) && uv run uvicorn chessmark.main:app --reload --port 8010

web: ## Run the frontend (port 3010)
	cd $(WEB) && pnpm dev

migrate: ## Apply all migrations
	cd $(API) && uv run alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add foo"
	cd $(API) && uv run alembic revision --autogenerate -m "$(m)"

drift: ## Fail if models and migrations disagree
	cd $(API) && uv run alembic check

seed-models: ## Load seeds/models.json into model_registry
	cd $(API) && uv run python ../../scripts/seed_models.py

refresh-models: ## Re-fetch the tool-capable model list from OpenRouter
	python3 scripts/refresh_model_seed.py

refresh-endpoints: ## Refresh which providers serve each model, and at what quantization
	cd $(API) && uv run python ../../scripts/refresh_endpoints.py

record-llm: ## Re-record LLM test fixtures (spends free-tier requests; never run by CI)
	cd $(API) && uv run python ../../scripts/record_llm_fixtures.py

smoke-llm: ## One real end-to-end LLM call. Manual only — the test suite never calls a provider
	cd $(API) && uv run python ../../scripts/smoke_llm.py

play: ## Play a full game and watch it. ARGS="--scripted" needs no API key
	cd $(API) && uv run python ../../scripts/play_game.py $(ARGS)

worker: ## Run a standalone turn worker
	cd $(API) && uv run python ../../scripts/worker.py

test: test-api test-web ## Run every test (database tests need `make up`; never calls a provider)

test-api: ## Run backend tests
	cd $(API) && uv run pytest

test-web: ## Run frontend unit tests (pure logic in src/lib)
	cd $(WEB) && pnpm test

test-unit: ## Run only tests that need no database
	cd $(API) && uv run pytest -m "not integration and not llm"

# Its own database: the suite drops and recreates the schema at session start, so a live run
# sharing `chessmark_test` with a concurrent `make test` would clobber it mid-game.
test-llm: ## Run the live-provider tests. Costs real requests — opt-in, never in CI
	cd $(API) && TEST_DATABASE_URL=postgresql+asyncpg://chessmark:chessmark@localhost:5433/chessmark_llm_test \
		uv run pytest -m llm -s -o addopts=""

cov: ## Run the chess-domain coverage gate (NFR-07)
	cd $(API) && uv run pytest tests/game --cov=chessmark.game --cov-report=term-missing --cov-fail-under=90

lint: ## Lint backend and frontend
	cd $(API) && uv run ruff check .
	cd $(API) && uv run ruff format --check .
	cd $(WEB) && pnpm lint

fmt: ## Format backend and frontend
	cd $(API) && uv run ruff format . && uv run ruff check --fix .

typecheck: ## Typecheck backend and frontend
	cd $(API) && uv run mypy src
	cd $(WEB) && pnpm exec next typegen && pnpm typecheck

sync-clerk-env: ## Copy Clerk keys from apps/web/.env.local into the root .env the API reads
	python3 scripts/sync_clerk_env.py

verify-clerk: ## Check the Clerk configuration against the real instance (AUTH-01)
	cd $(API) && uv run python ../../scripts/verify_clerk.py

bundle-secrets: ## Assert no API key reached the built client bundle (AUTH-07)
	cd $(WEB) && pnpm build
	python3 scripts/check_bundle_secrets.py

check: lint typecheck test ## Run every check

clean: ## Remove build artifacts and caches
	rm -rf $(WEB)/.next $(API)/.pytest_cache $(API)/.ruff_cache $(API)/.mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
