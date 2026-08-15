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

test: ## Run backend tests
	cd $(API) && uv run pytest

cov: ## Run the chess-domain coverage gate (NFR-07)
	cd $(API) && uv run pytest tests/game --cov=chessmark.game --cov-report=term-missing --cov-fail-under=90

lint: ## Lint backend and frontend
	cd $(API) && uv run ruff check .
	cd $(WEB) && pnpm lint

fmt: ## Format backend and frontend
	cd $(API) && uv run ruff format . && uv run ruff check --fix .

typecheck: ## Typecheck backend and frontend
	cd $(API) && uv run mypy src
	cd $(WEB) && pnpm exec next typegen && pnpm typecheck

check: lint typecheck test ## Run every check

clean: ## Remove build artifacts and caches
	rm -rf $(WEB)/.next $(API)/.pytest_cache $(API)/.ruff_cache $(API)/.mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
