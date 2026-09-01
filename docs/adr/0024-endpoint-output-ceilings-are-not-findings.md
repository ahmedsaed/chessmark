# 0024. An endpoint's output ceiling is not a finding about a model

**Status:** Accepted
**Date:** 2026-09-01
**Amends:** [0021](0021-measured-windows-and-the-compaction-ladder.md) — the half of it that kept
`TRUNCATED` rated when the *provider's* ceiling cut the answer. Extends
[0019](0019-harness-bounds-are-not-findings.md) to a third measurement that turned out to be about
the host rather than the weights.

## Context

The `pool-free` event ran 28 games to a result. One of them, `a016a326`, is the reason for this ADR.

`poolside/laguna-s-2.1` was White. At ply 69 it held a rook and two bishops against a lone black
pawn — material 22 to 1, with the black king on g8 and mate available in a handful of moves. It
lost the game 0–1, `truncated`, ranked.

### What actually happened

The turn made five calls. The first returned a tool call and worked. The next four each came back
`finish_reason: "length"` having generated **exactly 32,768 tokens**, all of them reasoning, none
of them content or a tool call. `MAX_TRUNCATIONS` was spent and the seat was forfeited.

Every one of those four requests asked for `max_tokens: 64000`.

Poolside will not emit more than 32,768 tokens in one response. It is in our own database:
`ModelEndpoint.max_completion_tokens`, synced from OpenRouter by `registry.py` since the registry
existed, **and never read by anything**. `compaction.Window` carried only `context`, so
`completion_cap` reconciled the request against the 256,000-token *window* and passed 64,000
straight through.

### Why the existing safeguard could not fire

ADR-0021 already excluded truncations we caused. `_our_ceiling_bound` asks whether the response
reached the number we requested: `generated >= requested`. Here that is `32768 >= 64000` — false —
so the harness concluded the endpoint had cut it, which under ADR-0021 was a finding about the
model.

The check is correct and was fed a number that could not occur. By asking for more than the
endpoint would ever return, we guaranteed that every truncation looked like the model's fault. The
safeguard was strictly weaker the more we over-asked.

### And the classification was wrong anyway

Suppose the arithmetic had been right and we had asked for exactly 32,768. The response would have
stopped at our ceiling, ADR-0021 would have excluded it, and the game would not have been decided
by a forfeit. The *same physical event* — Poolside stopping at 32,768 — would have been read two
different ways depending only on what we had typed in the request.

That is the tell. What the ceiling measures is the endpoint. The same weights served by a host with
a 64,000-token response limit are not cut off, and ADR-0019 has already rejected exactly this shape
of claim twice: `TIMEOUT` measured the provider's latency, `BUDGET_EXCEEDED` measured our own
prompt replay, and both stopped being findings. ADR-0021's reasoning for keeping `TRUNCATED` — that
the provider's ceiling is "a budget set well above what any of them need" — treats a property of
the host as though it were a property of the task.

## Decision

**Two changes, and the second is not optional given the first.**

1. **Ask for what the endpoint will give.** `Window` carries `max_completion` alongside `context`,
   read from `ModelEndpoint.max_completion_tokens`, and `completion_cap` clamps to it. It binds
   even when the context window is unknown, because it is a flat fact about the endpoint rather
   than arithmetic over the transcript. Where an endpoint declares nothing, unknown stays unknown
   and the request goes out as before.

2. **A truncation is never a finding.** `TRUNCATED` leaves `FORFEIT_TERMINATIONS` and
   `RATED_TERMINATIONS` and joins `HARNESS_TERMINATIONS` and `RESUMABLE_TERMINATIONS`. When the
   retries are spent the turn fails and the worker decides, exactly as for a provider outage.

Change 1 alone would not have been enough. It makes the common case attributable, but it leaves the
verdict depending on the registry being current: an endpoint that lowers its ceiling, or one whose
row is stale, puts us straight back to over-asking and calling the result a model failure. Change 2
is what makes the answer not depend on our bookkeeping.

The in-turn retry survives both. A model told it was cut off frequently acts on the next attempt,
and that is worth more than failing the turn on the first `length`. Only the ending changed.

### Two consequences that had to be fixed with it

**`Player.forfeited` was written by the turn's status rather than by the ending.**
`turn.py` set it from `result.status is TurnStatus.FORFEITED`, and `BUDGET_EXCEEDED` travels that
way because it does end the game — while `ratable.HARNESS_TERMINATIONS` says just as plainly that
it is not a finding. The flag is published: `bench.service` counts it into the leaderboard's
forfeits column. Two games in this pool were budget-stopped, reopened, and played on to a genuine
checkmate and a genuine threefold draw; both stayed ratable and both models carried a forfeit
nothing in their play had earned. It now follows `FORFEIT_TERMINATIONS`.

**`resume` did not clear that flag.** It already un-settles the pairing — both `abandoned_reason`
and `white_score`, after clearing only the first left four resumed games drawn as *played* with the
score of the forfeit that had just been overturned. `Player.forfeited` is the same kind of thing,
written by the same ending, and was missed in the same way. It is cleared now, unless the ending
being reopened was itself a forfeit — which no resumable termination is, so the guard exists to
keep the function honest rather than to fire.

**`resume --harness-ceiling` is removed.** It reopened a `truncated` forfeit where the stored calls
showed our own `max_tokens` had cut the response, and refused when the endpoint's had. That
question no longer has two answers, so `TRUNCATED` is resumable outright and the flag could never
have fired again. A gate that cannot fire is worse than no gate: it implies a distinction that is
no longer being drawn.

## Alternatives considered

**Clamp the request and leave the rating alone.** The cheapest change, and it fixes the game that
prompted this. It leaves the verdict resting on `ModelEndpoint.max_completion_tokens` being
accurate for every endpoint, forever — a column nothing had read until today, which is not a thing
to hang a published rating on.

**A separate `ENDPOINT_CEILING` termination.** Honest about the distinction, and it keeps
`TRUNCATED` meaning "we cut it". But it needs a migration, a UI string and a fifth membership in
four frozensets, to name a case that after change 1 should be rare and is unrated either way. The
distinction is already in the record — the request says what we asked for, the response says what
came back — and that is where it belongs.

**Cap reasoning explicitly** (`reasoning: {max_tokens: …}`). Would have prevented the runaway at
source. It also changes what a ranked game *is* (invariant 5) and would need a prompt-version bump
and a re-run of every rating. Worth considering on its own merits; not as part of a correction.

**Leave the result.** The model did produce 32,768 tokens of reasoning four times without acting,
and there is an argument that failing to act inside any real ceiling is a finding. But it is the
argument `TIMEOUT` already lost: a slow provider does not make a model worse, and a small response
ceiling does not either. It is the routing lottery ADR-0015 exists to remove, wearing a third hat.

## Consequences

**Games already recorded are reclassified when this ships.** Ratings are recomputed from
`termination` on every run, so every historical `truncated` game leaves the rated set the moment
this deploys — no backfill, and no migration. `a016a326` stops counting against
`poolside/laguna-s-2.1`, and becomes resumable with a plain `./chessmark resume`.

**`TRUNCATED` as a forfeit is now effectively unreachable.** Either we cut the answer or the
endpoint did, and both fail the turn. The enum member stays, because historical games carry it and
`referee.forfeit` is still the mechanism the turn loop used to reach it.

**A model that cannot finish a turn now abandons its game instead of losing it.** The turn fails,
the worker retries to `MAX_JOB_ATTEMPTS` and abandons — visible on the site, excluded from ratings,
and reopenable. That is the honest outcome and it is *less* informative than a forfeit was: a model
that genuinely rambles forever is now recorded as a game we could not run. The compensation is that
the number is still published — reasoning tokens per call, per contestant — as a statistic, which
is what it is.

**What to watch.** If abandonments attributable to truncation become common on a model that other
endpoints serve fine, that is the routing lottery talking and the answer is endpoint selection, not
a rule change. If they become common on a model *every* endpoint truncates, this ADR is hiding a
real finding and the case for a rated `ENDPOINT_CEILING` reopens with evidence behind it.
