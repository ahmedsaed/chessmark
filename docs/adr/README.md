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
| [0007](0007-turn-level-jobs.md) | The turn is the unit of work | Amended by [0022](0022-one-owner-per-ply.md) |
| [0008](0008-game-events-log.md) | A single `game_events` log powers live, reconnect, and replay | Accepted |
| [0009](0009-dedicated-say-tool.md) | Trash talk via a dedicated `say` tool | Accepted |
| [0010](0010-defer-stockfish.md) | Defer Stockfish, but ship the schema for it now | Accepted |
| [0011](0011-server-keys-layered-budgets.md) | Server-held API keys with four layers of budget control | Amended by [0023](0023-one-source-of-truth-for-the-free-allowance.md) |
| [0012](0012-nonstandard-local-ports.md) | Non-standard local ports | Accepted |
| [0013](0013-design-system.md) | Design system: Board & Amber, dark only, conversation-led | Accepted |
| [0014](0014-provider-routing-and-quantization.md) | Pin provider routing and exclude sub-8-bit quantization | Superseded in part by [0015](0015-quantization-as-identity-and-pinned-endpoints.md) |
| [0015](0015-quantization-as-identity-and-pinned-endpoints.md) | Quantization identifies the contestant; endpoints are pinned per match | Amended by [0019](0019-harness-bounds-are-not-findings.md), [0027](0027-a-pool-is-ranked-by-its-own-rating.md) |
| [0016](0016-credits-as-a-granted-balance.md) | Credits are a granted balance, priced per model | Accepted |
| [0017](0017-rate-limits-pause-games.md) | A rate limit pauses the game; endpoints cool down between games | Amended by [0025](0025-finishing-a-game-beats-starting-one.md) |
| [0018](0018-context-compaction.md) | The model summarises its own history when the window fills | Amended by [0021](0021-measured-windows-and-the-compaction-ladder.md) |
| [0019](0019-harness-bounds-are-not-findings.md) | A harness bound is not a finding about a player | Amended by [0021](0021-measured-windows-and-the-compaction-ladder.md), [0026](0026-a-repeated-question-gets-a-different-answer.md) |
| [0020](0020-claimable-draws.md) | Threefold and the fifty-move rule are claimed, not applied | Accepted |
| [0021](0021-measured-windows-and-the-compaction-ladder.md) | The window is measured, and compaction trims before it summarises | Amended by [0024](0024-endpoint-output-ceilings-are-not-findings.md) |
| [0022](0022-one-owner-per-ply.md) | A ply has one owner, and a game that ended stays ended | Accepted |
| [0023](0023-one-source-of-truth-for-the-free-allowance.md) | The free allowance is OpenRouter's number, not ours | Accepted |
| [0024](0024-endpoint-output-ceilings-are-not-findings.md) | An endpoint's output ceiling is not a finding about a model | Accepted |
| [0025](0025-finishing-a-game-beats-starting-one.md) | A game due to resume keeps its slot, and patience is measured from the last move | Accepted |
| [0026](0026-a-repeated-question-gets-a-different-answer.md) | A repeated read-only tool call is answered with a nudge | Accepted |
| [0027](0027-a-pool-is-ranked-by-its-own-rating.md) | A pool is ranked by a rating over its own games; a closed event by points | Amended by [0028](0028-a-wider-prior-and-a-provisional-mark.md) |
| [0028](0028-a-wider-prior-and-a-provisional-mark.md) | A wider prior, and a rating that says when it is not settled | Amended by [0029](0029-a-deviation-has-a-ceiling.md) |
| [0029](0029-a-deviation-has-a-ceiling.md) | A rating deviation is capped at the prior | Accepted |
| [0030](0030-a-halt-pauses-the-board.md) | A halt pauses every game it covers, and says so on the page | Accepted |

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
