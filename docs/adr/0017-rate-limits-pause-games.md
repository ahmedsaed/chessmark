# 0017. A rate limit pauses the game; endpoints cool down between games

**Status:** Accepted
**Date:** 2026-08-27
**Amends:** the retry policy in [0002](0002-illegal-move-policy.md)'s sibling — provider-failure
handling in `agents/llm.py` and `orchestration/worker.py`. Reinforces
[0015](0015-quantization-as-identity-and-pinned-endpoints.md) from the provider's side.

## Context

A free-model pool ran for ninety minutes on 26 August and produced nothing. Fourteen consecutive
games were marked **abandoned at ply 0**, every one of them the same model, on the same endpoint,
with the same error:

```
google/gemma-4-26b-a4b-it:free is temporarily rate-limited upstream
"limit_source": "upstream_provider_shared_pool"   (status=429, attempts=8)
```

Four separate mistakes compounded, and it is worth naming them apart because only one of them looks
like the obvious problem.

**A 429 was treated as a transient failure to retry harder at.** The gateway made 8 attempts with
an exponential ladder; the worker then requeued the job **with no delay** up to 5 times. Forty
requests per game, six and a half minutes, then abandonment — around 560 doomed requests across the
incident, every one of them charged against the same daily allowance the retries were nominally
protecting. The ladder had also been mis-sized: rate-limit delays came from `base_delay = 0.5s`, so
eight doublings reached 32 seconds and the `rate_limit_max_delay = 300` ceiling never bound.

**Waiting was a decision the retry loop could not make.** A previous fix had gone the *wrong* way,
raising rate-limit attempts from 4 to 8 on the reasoning that "a tournament has no deadline, so
waiting is free". That is true of the game and false of the request: every attempt spends the
scarce thing.

**Abandonment was the wrong outcome.** `aborted` is a claim that a game will not continue. The
provider was working, the position was untouched, and neither model had done anything — the game
had simply not happened yet.

**And the pool re-paired the same model, forever.** This is the one that made ninety minutes out of
what should have been one failure. A pool's matchmaker plays whoever it knows least about, an
abandoned game is excluded from ratings, so the failing entrant's deviation never moved and it
stayed the least-known entrant. `Form.games` was never populated, so the tie-break fell to the
**alphabetical key** — a deterministic loop in which the one model that could not play was the one
the pool would choose next, every time. Nothing in the system learned from failure.

Two facts from OpenRouter frame the fix. A `Retry-After` header is sent **only when every attempted
provider returned a retry hint**, and every free model is served by exactly one endpoint — checked,
all fifteen — so the incident carried no hint at all and had nowhere to fall back to. The
documented remedy, "relax provider routing so more providers are eligible", does not exist for a
free model.

## Decision

**A rate limit pauses the game.** `GameStatus.PAUSED` is a live game stopped by something outside
it, with `resume_after` and a one-line `pause_reason`. It keeps its transcript, appends exactly one
`game_paused` event, and is resumed by the reconciler when the wait is over. It is not a
termination and never reaches the ratings.

**A paused game holds no concurrency slot.** `in_flight` counts games that are `PENDING` or
`RUNNING`, so a pool with `max_concurrent = 1` gets on with an entrant that can play. This is a
deliberate loosening: two games may *exist* where one was allowed, but only one is spending, which
is what the bound is for.

**The gateway retries less, not more.** Three attempts on a rate limit against four on anything
else, on a ladder of its own (5s → 60s) so the ceiling binds. Patience moved out of the retry loop
and into a pause, which spends nothing to pass the time.

**A refusal is remembered between games.** `core/cooldown.py` keys a cooldown by (model, endpoint)
in Redis, escalating 1 → 5 → 15 → 30 → 60 minutes over consecutive refusals and honouring a
provider's own hint when there is one. A successful turn clears it, so the ladder cannot only climb.

**The matchmaker skips entrants that are resting.** Skipped, not withdrawn — a withdrawal is a
statement about the event and abandons that entrant's pairings; this is a statement about the next
few minutes, and the entrant returns by itself. This is the half that breaks the loop.

**Patience is bounded.** Six pauses, then the game is abandoned with an honest reason. A game with a
human seat gets two pauses of at most two minutes: a person will not wait out a shared pool, and a
board that quietly never moves again is worse than one that says it gave up.

## Alternatives considered

**Retry harder, wait longer in the gateway.** The previous fix, and the incident is what it looks
like at scale. Every unit of patience inside the retry loop costs a request.

**Withdraw an entrant whose games keep failing.** Too strong, and irreversible in the wrong
direction: a provider hot for an afternoon would remove a model from a pool permanently, and its
unplayed pairings would be abandoned. A cooldown expires on its own.

**Abandon at ply 0 and re-pair later.** Nothing is lost at ply 0, so this is defensible — but it
needs the cooldown anyway to avoid re-pairing the same model, and with the cooldown in place the
pause costs nothing and needs no second mechanism. One path for both cases.

**Route around it with `allow_fallbacks`.** OpenRouter's own advice, and inapplicable: a free model
has one endpoint.

## Consequences

A pool survives a provider outage instead of burning its allowance through it. The catalogue's
free tier is patchy by nature, so this is the difference between a pool that measures models and
one that measures endpoint availability.

**A game can now sit paused for up to about three and a half hours**, which is visible to readers:
the lobby card, the game header and the event stream all say paused and why. That is a deliberate
trade against the alternative of a false `abandoned` record.

**`GameStatus` gained a value with no migration**, because the enum is `native_enum=False` with no
CHECK constraint — Python is the source of truth (`db/base.py`). Only the two new nullable columns
needed DDL. Old `aborted` games that *were* rate limits stay aborted: reclassifying finished
records from a string match on their termination detail would be a guess written into history.

**The reconciler now runs every minute rather than every five**, because it decides when a paused
game comes back and the shortest cooldown rung is sixty seconds.

Not addressed here: the same incident showed a call running **561 seconds** against a turn deadline
that had 40 seconds left, and free models averaging 17–38s per call. Slow free endpoints are a
separate problem from refused ones.


## Amendment — 2026-08-27, hours later: what production added

Deployed within the day, and the pool told us two things the tests could not.

**A shared pool is the provider's, and the cooldown was keyed too narrowly.** `gemma-4-26b` was
cooled down and correctly skipped — so the matchmaker paired `gemma-4-31b`, a *different model on
the same hot Google AI Studio pool*, which paused a minute later for the same reason. Then a third,
on GMICloud. Three models, two providers, four paused games, each rediscovering one fact.
`limit_source: upstream_provider_shared_pool` is a statement about a pool serving many models, so a
refusal naming it now rests the **whole provider**, and an entrant is skipped when *every* endpoint
it has is on a resting provider. "Every" rather than "any": a paid model on nineteen providers is
not unavailable because one rests, and the router would simply pick another.

**Freeing the concurrency slot needs a ceiling.** Not counting paused games was right — they spend
nothing — but with nothing bounding the inactive pile, a hot provider is absorbed by opening more
games rather than by waiting. Four paused games stood against a concurrency of one, and each would
wake, be refused, and eventually abandon: the original failure at a slower tempo. A pool now holds
once `max_concurrent + 2` games are paused. The headroom exists so one unlucky pause does not stall
a healthy pool, not so a provider outage can be spread across the field.

**And one abandonment was never a rate limit at all.** A game died at ply 10 on a 400: *"maximum
context length is 65536 tokens, however you requested about 65810"* — `liquid/lfm-2.5-2.6b:free`,
whose window is under the 128k floor AGENT-14 sets, asked for a flat 64,000-token completion. The
gateway classified it as fatal and tried once; **the worker requeued it four more times**, because a
`TurnResult` carried the error's text and nothing that could be reasoned about. A refusal of the
*request* is now abandoned at once. That is deliberately narrower than the gateway's `retryable`:
a deadline is fatal to a call and not to a turn, and an auth error must not abandon every game in
flight, so only request-shape rejections short-circuit.

Still open, and stated rather than fixed: `max_completion_tokens` is a flat 64,000 that nothing
reconciles against the window of the endpoint serving it.
