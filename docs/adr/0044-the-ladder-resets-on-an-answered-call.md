# ADR-0044: The cooldown ladder resets on an answered call

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** [ADR-0015](0015-quantization-as-identity-and-pinned-endpoints.md), [ADR-0017](0017-rate-limits-pause-games.md)

## Context

A game started by hand on production sat at ply 0 for an hour without playing a move:

```
15:48:31  pause 1   deepseek-v4.1-flash rate-limited by BaseTen (upstream_provider_shared_pool)
15:49:49  resume
15:50:24  pause 2   next attempt in 5 minutes
15:55:24  pause 3   next attempt in 15 minutes
```

Every pause payload carried `retry_after_seconds: null`, so the provider offered no hint and the
whole schedule was ours: `LADDER_SECONDS = (60, 300, 900, 1800, 3600)`.

`upstream_provider_shared_pool` is OpenRouter's marker for the **upstream provider's** shared
capacity being hot. It is not about our account and not about the model being paid — which is why a
paid model carries it, and why waiting is the right response to it.

The ladder is right to escalate against an endpoint that has gone away. The fault is in what it
counted as evidence that one had.

`cooldown.clear()` was called from the worker once a **turn** committed. A turn is many provider
calls against a growing transcript, so *"it finished a turn"* is a far stronger claim than *"it is
serving"*. An endpoint that answered the board read and was refused on the move never reached the
clear, so its strike count only climbed — to the hour cap, with strikes remembered for six hours —
against an endpoint that had answered a minute earlier.

The direction is the perverse part. **The longer the transcript the less likely a turn completes**,
so the ladder was harshest on exactly the endpoint a long game most needs, and it read an endpoint
that *was* answering as one that had stopped.

## Decision

Reset the ladder on an **answered call**, not a finished turn.

`LlmGateway` gains `on_success`, wired by the worker to the cooldown. A call that came back is proof
the endpoint has not gone away, which is the only thing the ladder exists to detect.

The provider is taken from the **response** rather than from the seat's pin. What answered is what
should be credited; the two coincide for a pinned seat (ADR-0015) and need not for anything else.

The hook is public and set by the worker rather than passed to the constructor, because the gateway
is handed in already built and the thing that remembers endpoints is the worker's cooldown. The
gateway itself still remembers nothing between calls.

## Consequences

An endpoint that answers and is then refused rests sixty seconds instead of half an hour. A refusal
that follows no answer at all still escalates exactly as before — that is what the ladder is for,
and a test pins it at `LADDER_SECONDS[3]`.

The matchmaker reads the same cooldown, so this also stops a pool skipping an entrant whose endpoint
is in fact serving.

**What this does not change.** A seat is still pinned to one endpoint for the whole game, rated or
not (ADR-0015), so a game against a contended endpoint still waits for *that* endpoint and can still
spend its patience window there. Pinning is what makes a result reproducible and the behaviour is
deliberately the same for every match; this ADR only stops the waiting compounding against an
endpoint that never stopped answering.
