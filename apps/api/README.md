# Chessmark API

FastAPI backend: agent runtime, chess game engine, persistence, and the SSE stream that feeds the
frontend.

## Setup

```bash
uv sync --all-groups
cp ../../.env.example ../../.env   # then fill in secrets
```

## Run

```bash
uv run uvicorn chessmark.main:app --reload --port 8000
```

Docs at http://localhost:8000/docs

## Checks

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # types
```

## Layout

| Path | Purpose |
| --- | --- |
| `src/chessmark/api/` | HTTP routes, request/response schemas, SSE endpoints |
| `src/chessmark/agents/` | LLM agent runtime: tool definitions, turn loop, transcript handling |
| `src/chessmark/game/` | Chess rules, match orchestration, referee, clocks |
| `src/chessmark/db/` | SQLAlchemy models, session management, repositories |
| `src/chessmark/core/` | Config, logging, errors, shared utilities |
