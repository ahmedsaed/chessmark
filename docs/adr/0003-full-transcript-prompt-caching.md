# 0003. Full game transcript, engineered for prompt caching

**Status:** Accepted
**Date:** 2026-08-15

## Context

An agent must have history across the whole game — that is a product requirement, not an
optimisation choice. But naïve full history costs O(n²) prompt tokens over a game: turn 60 replays
everything from turns 1–59. A 60-move game against a frontier model could run to several dollars,
which kills a free public product.

Provider prompt caching makes the replayed prefix dramatically cheaper — but only if that prefix is
**byte-identical** between calls. Caching is not a flag you set; it's a property you preserve.

## Decision

Keep the full transcript, and engineer the message list so it caches:

- The **system prompt is static** for the entire game. No move number, no clock, no score, no
  "you are on move 24". Anything that changes goes into the message body.
- Messages are strictly **append-only**. No summarising, rewriting, or reordering of earlier turns —
  ever. Each turn extends the previous list; it never edits it.
- Cache breakpoints after the system prompt and at a rolling recent boundary.
- `cached_tokens` is recorded on every call, and cache hit rate is a tracked metric (NFR-06, >80%).

Phase 4 has an explicit test asserting that turn N+1's message list is a **strict prefix-extension**
of turn N's, compared byte-wise. That test is the real enforcement mechanism.

## Alternatives considered

- **Naïve full history.** Same fidelity, several times the cost. Only differs by not caring.
- **Sliding window + board state.** Bounded cost, but it throws away long-range plan continuity —
  and "does the model remember its own plan from 30 moves ago?" is exactly what we want to measure.
- **Running summarisation.** Rewrites the prefix on every turn, which destroys caching *and*
  introduces a lossy step we'd then have to defend as part of the benchmark.

## Consequences

- Cost stays roughly linear rather than quadratic. This is the difference between viable and not.
- Any code that touches the message list is now safety-critical. A single injected timestamp silently
  drops the hit rate to zero and multiplies cost — hence the byte-wise test.
- Games are bounded by the model's context window. Long games against small-context models will hit
  the ceiling; we detect it before the call and end the game as `context_exceeded` rather than
  truncating quietly (see REQUIREMENTS risk table).
- Cache TTLs are short and provider-specific. A game that stalls may cold-start its cache. Turn
  latency matters for cost, not just UX.
- Windowed context remains available as a **recorded per-game parameter** (AGENT-13), so full vs.
  windowed becomes an ablation we can run rather than an assumption we bake in.
