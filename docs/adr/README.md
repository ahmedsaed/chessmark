# Architecture Decision Records

Each ADR records one decision: the context that forced it, what was chosen, what was rejected, and
what we now have to live with. They are immutable — a decision that changes gets a **new** ADR that
supersedes the old one, rather than an edit.

**The one exception is the number, and only when two ADRs share one.** Immutability protects the
*decision*; a filing error in which number it was given protects nothing. Two ADRs were both
numbered 0032, and the collision was not cosmetic: `CHANGELOG.md` resolved a line about the
leaderboard to the context arithmetic, and two source files cited "0032" meaning different
decisions. The later one is renumbered, its content unchanged, and a **pointer** is left at the old
path with `**Status:** Renumbered` so links written against it still land. See
[0032](0032-the-arithmetic-that-decides-a-request.md) for what one looks like.

`apps/api/tests/docs/test_adr_integrity.py` runs in `make check` and fails on a duplicate number, an
ADR missing from the table below, an index row pointing at nothing, a header that is not the format
used here, and any relative link or heading anchor in our markdown that does not resolve. All five
were live in this directory before it was written.

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
| [0016](0016-credits-as-a-granted-balance.md) | Credits are a granted balance, priced per model | Superseded by [0052](0052-credit-is-dollars-spent-at-actual-cost.md) |
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
| [0031](0031-a-turn-may-not-inflate-its-own-context.md) | A turn may not inflate its own context | Accepted |
| [0032](0032-the-leaderboard-is-stored-not-recomputed-per-request.md) | The leaderboard is stored, not recomputed on every request | Accepted |
| [0033](0033-a-tail-budget-in-tokens-and-a-provider-that-cannot-count.md) | A tail budget in tokens, and a provider that cannot count | Accepted |
| [0034](0034-one-page-per-model.md) | One page per model, and the contestant is a section on it | Accepted |
| [0035](0035-live-frames-are-not-events.md) | A turn streams as it happens, and what streams is not an event | Accepted |
| [0036](0036-a-lost-reasoning-trace-announces-itself.md) | A lost reasoning trace announces itself, so streaming can be on | Accepted |
| [0037](0037-a-turn-ends-when-the-model-stops.md) | A turn ends when the model stops, not when it moves | Accepted |
| [0038](0038-a-prompt-version-has-two-parts.md) | A prompt version has two parts, and only one invalidates a result | Accepted |
| [0039](0039-the-window-is-sized-for-the-request-in-front-of-us.md) | The window is sized for the request in front of us, not the one behind it | Accepted |
| [0040](0040-what-a-board-shows-and-what-the-prompt-owes-you.md) | What a board shows, and what the prompt owes you | Accepted |
| [0041](0041-a-pool-balances-its-pairings.md) | A pool balances its pairings, and does it without a schedule | Accepted |
| [0042](0042-the-notation-was-still-analysing-the-position.md) | The notation was still analysing the position | Accepted |
| [0043](0043-a-pool-carries-its-eras.md) | A pool carries its eras, because a pool never ends | Accepted |
| [0044](0044-the-ladder-resets-on-an-answered-call.md) | The cooldown ladder resets on an answered call, not a finished turn | Accepted |
| [0045](0045-a-turn-keeps-the-rounds-it-completed.md) | A turn keeps the rounds it completed, and the retry continues it | Proposed |
| [0046](0046-the-api-invalidates-the-cache-a-clock-does-not.md) | The API invalidates the frontend's cache; a clock does not | Accepted |
| [0047](0047-the-arithmetic-that-decides-a-request.md) | The arithmetic that decides whether a request can be sent | Accepted |
| [0048](0048-the-archive-filters-on-the-server-and-pages-by-keyset.md) | The archive filters on the server, and pages by keyset | Accepted |
| [0049](0049-decision-models-play-through-their-own-harness.md) | Decision models play through their own harness | Accepted |
| [0050](0050-a-pool-saturates-per-pair.md) | A pool saturates per pair | Accepted |
| [0051](0051-a-decision-model-chooses-its-action-and-is-checked-before-it-plays.md) | A decision model chooses its action, and is checked before it plays | Accepted |
| [0052](0052-credit-is-dollars-spent-at-actual-cost.md) | Credit is dollars, spent at what each turn actually cost | Accepted |

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
