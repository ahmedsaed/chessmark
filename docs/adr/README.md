# Architecture Decision Records

Each ADR records one decision: the context that forced it, what was chosen, what was rejected, and
what we now have to live with. They are immutable — a decision that changes gets a **new** ADR that
supersedes the old one, rather than an edit.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-monorepo-fastapi-nextjs.md) | Monorepo with FastAPI + Next.js | Accepted |
| [0002](0002-illegal-move-policy.md) | Illegal moves: full legal list on error, forfeit after 5 retries | Accepted |
| [0003](0003-full-transcript-prompt-caching.md) | Full game transcript, engineered for prompt caching | Accepted |
| [0004](0004-sse-over-websockets.md) | SSE instead of WebSockets for live updates | Accepted |
| [0005](0005-postgres-from-day-one.md) | PostgreSQL from day one | Accepted |
| [0006](0006-clerk-for-auth.md) | Clerk for authentication | Accepted |
| [0007](0007-turn-level-jobs.md) | The turn is the unit of work | Accepted |
| [0008](0008-game-events-log.md) | A single `game_events` log powers live, reconnect, and replay | Accepted |
| [0009](0009-dedicated-say-tool.md) | Trash talk via a dedicated `say` tool | Accepted |
| [0010](0010-defer-stockfish.md) | Defer Stockfish, but ship the schema for it now | Accepted |
| [0011](0011-server-keys-layered-budgets.md) | Server-held API keys with four layers of budget control | Accepted |
| [0012](0012-nonstandard-local-ports.md) | Non-standard local ports | Accepted |
| [0013](0013-design-system.md) | Design system: Board & Amber, dark only, conversation-led | Accepted |
| [0014](0014-provider-routing-and-quantization.md) | Pin provider routing and exclude sub-8-bit quantization | Superseded in part by [0015](0015-quantization-as-identity-and-pinned-endpoints.md) |
| [0015](0015-quantization-as-identity-and-pinned-endpoints.md) | Quantization identifies the contestant; endpoints are pinned per match | Accepted |
| [0016](0016-credits-as-a-granted-balance.md) | Credits are a granted balance, priced per model | Accepted |
| [0017](0017-rate-limits-pause-games.md) | A rate limit pauses the game; endpoints cool down between games | Accepted |
| [0018](0018-context-compaction.md) | The model summarises its own history when the window fills | Accepted |
| [0019](0019-harness-bounds-are-not-findings.md) | A harness bound is not a finding about a player | Accepted |
| [0020](0020-claimable-draws.md) | Threefold and the fifty-move rule are claimed, not applied | Accepted |

## Template

```markdown
# NNNN. Title

**Status:** Proposed | Accepted | Superseded by [NNNN](...)
**Date:** YYYY-MM-DD

## Context
What forces this decision? What constraints are real?

## Decision
What we're doing. Stated plainly.

## Alternatives considered
What else was on the table, and why it lost.

## Consequences
What this buys us. What it costs us. What we now have to watch.
```
