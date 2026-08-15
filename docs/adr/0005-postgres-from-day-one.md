# 0005. PostgreSQL from day one

**Status:** Accepted
**Date:** 2026-08-15

## Context

Chessmark's core promise is that *everything* is stored: verbatim LLM requests and responses,
reasoning traces, every tool call, every event. That's a write-heavy, semi-structured, concurrent
workload — many workers writing to many games at once — with an ordering guarantee (`game_events.seq`)
that must hold under concurrency.

## Decision

PostgreSQL 16 from the first line of persistence code. Local development runs it in Docker Compose;
production runs it on the VPS. SQLAlchemy 2 (async, with `asyncpg`) plus Alembic.

## Alternatives considered

- **SQLite first, migrate later.** Zero infrastructure, genuinely pleasant for a single developer.
  But its single-writer model is a poor fit for concurrent workers, and the migration would land
  exactly when the system is most load-bearing. The saved setup time is one `docker compose up`.
- **MongoDB.** Natural for verbatim JSON blobs, but the relational core — games, players, plies,
  ratings — is genuinely relational, and we want real foreign keys and transactional integrity on
  the game record.

## Consequences

- `JSONB` handles verbatim payloads natively, with indexing available if we need to query into them.
- Real transactions mean a ply, its turn, its LLM calls, its tool calls, and its cost rollup all
  commit atomically — this is what makes "a partial outage never corrupts a game record" (NFR-08)
  achievable rather than aspirational.
- Concurrent workers are a non-issue rather than a design constraint.
- Requires Docker locally. This was the deciding practical cost and it has already been resolved.
- Large verbatim payloads will bloat tables over time. Mitigation is planned (LOG-05: offload to
  object storage, referenced by key), not assumed away.
