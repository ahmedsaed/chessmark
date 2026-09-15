# ADR-0044: A contended endpoint should not end a game

**Status:** accepted · **Date:** 2026-09-15 · **Amends:** [ADR-0015](0015-quantization-as-identity-and-pinned-endpoints.md)

## Context

A game started by hand on production sat at ply 0 for an hour without playing a move:

```
15:48:31  pause 1   deepseek-v4.1-flash rate-limited by BaseTen (upstream_provider_shared_pool)
15:49:49  resume
15:50:24  pause 2   next attempt in 5 minutes
15:55:24  pause 3   next attempt in 15 minutes
```

Nothing was broken. Two correct mechanisms, each right on its own, combined into a game that could
not be played.

**`upstream_provider_shared_pool` is not about us.** It is OpenRouter's marker for the upstream
provider's own shared capacity being hot. It has nothing to do with whether the model is paid, or
with our account's credit — which is why a *paid* model can carry it.

### Pinning applied to games that gain nothing from it

ADR-0015 pins one endpoint per seat for the whole game, so a rated number is reproducible. The
first paid benchmark was served by two endpoints inside one game and measures a blend nothing can
repeat; that is a real problem and pinning is the right answer to it.

`resolve_routing` never asked whether the game was rated. The game above was `is_ranked: false` —
an exhibition somebody started to watch. `deepseek-v4.1-flash` has **eighteen endpoints**; the game
was pinned to the one that was throttled and spent its life climbing the ladder while seventeen
others answered. Reproducibility bought nothing, because nothing was going to be reproduced.

### The cooldown ladder reset on the wrong event

`cooldown.clear()` was called from the worker once a **turn** committed. A turn is many provider
calls against a growing transcript, so "it finished a turn" is a far stronger claim than "it is
serving". An endpoint that answered the board read and was refused on the move never reached the
clear, so its strike count only climbed: 60s, 300s, 900s, 1800s, to the hour cap, with strikes
remembered for six hours.

The perverse part is the direction. The longer the transcript the less likely a turn completes, so
the ladder was harshest on exactly the endpoint a long game most needs — and it read an endpoint
that *was* answering as one that had gone away.

## Decision

**Pin only a game that will be rated.** `resolve_routing` takes `pin`, and `create_match` passes
`is_ranked`. Two instructions are still honoured whatever the game:

* an explicit `provider`, because it is what tells a model's fault from its host's, and that
  investigation is never a rated game;
* an explicit `quantization`, because the precision lives on the endpoint — leaving the router free
  would seat `model@fp8` for a caller who asked for `model@fp4`, and those are different
  contestants (ADR-0015). A precision nothing serves still refuses the match, checked before `pin`
  is consulted so going unpinned cannot turn a caller's mistake into a quietly different opponent.

**Reset the ladder on an answered call, not a finished turn.** `LlmGateway` gains `on_success`,
wired by the worker to the cooldown. A call that came back is proof the endpoint has not gone away,
which is the only thing the ladder exists to detect. The provider is taken from the **response**
rather than from the seat's pin: what answered is what should be credited, and on an unpinned seat
those are not always the same endpoint.

## Consequences

An unranked game now uses every endpoint its model has, and records what actually served it —
`providers_used` already did, and the UI already warns when a game was served by more than one.
That warning stops being a fault report on an unranked game and becomes what it always described:
this result mixes endpoints and is not reproducible. Which is fine, because it is not rated.

A rated game is unchanged. It pins, for the reason ADR-0015 gives.

The ladder still escalates for an endpoint that says nothing at all — that is what it is for, and a
test pins it. What it no longer does is escalate against an endpoint that is answering.

**What this does not fix.** A rated game pinned to a contended endpoint still has no way around it,
and will still spend its patience window there. That is ADR-0015's trade and this ADR leaves it
standing: for a number that goes on a leaderboard, a game that fails honestly is better than one
served by whatever was free. If that becomes the common failure, the answer is to re-pin on the
*next* game rather than to un-pin this one.
