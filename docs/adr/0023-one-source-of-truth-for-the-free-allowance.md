# 0023. The free allowance is OpenRouter's number, not ours

**Status:** Accepted
**Date:** 2026-08-31
**Supersedes** the counting half of OPS-10 and amends
[0011](0011-server-keys-layered-budgets.md) — one of the four budget layers is removed rather than
fixed. Amends [0019](0019-harness-bounds-are-not-findings.md): a halt scoped to free models is a
harness stop that must not reach a paid game.

## Context

OpenRouter caps free models at 1,000 requests a day across the account (50 before ten credits are
bought). No response header reports what is left, so `core.budget.FreeTierBudget` kept our own
tally: `on_attempt` incremented a Redis counter before every call to a `:free` model, and a reserve
of 100 was held back so an unattended pool could not spend the last of the day.

**The count was designed to be wrong.** It incremented *before* the call, so gateway retries, calls
that died on our own ten-minute deadline, and compaction calls were all in it — deliberately, since
a failed call writes no `llm_calls` row and a count taken from the database would undercount. The
reserve existed to absorb that error.

Then it stopped play. `./chessmark status` reported **1,010 attempts of 900 usable**, the harness
refused every free turn, and three games froze mid-play. OpenRouter had not refused anything: no
`free-models-per-day` 429 had arrived, and the harness was not halted. Our number said stop; theirs
said carry on. Ours won, and a pool of free models — which is every pool we run — lost the rest of
the UTC day.

Two things had changed under it. The counter used to gate only *starting* a game, where being early
costs a delayed pairing; extending it to gate a **turn** (OPS-20) meant a game already in play
froze, and `FREE_TIER_SPENT` wrote no event, so the page showed a board stopping with nothing to
say. And OpenRouter's own signal had become available: the daily cap arrives as a 429 naming
`free-models-per-day`, carrying `X-RateLimit-Reset`, and it already halts the harness (OPS-19).

So there were two sources of truth for one number. One is an over-count maintained by us; the other
is the authority, exact, and tells us when it lifts.

## Decision

**`FreeTierBudget` is deleted.** Not tuned, not moved behind a bigger reserve — removed, with the
`on_attempt` counting that fed it and the checks in the worker and the tournament runner.

**OpenRouter's 429 is the only thing that stops us for the allowance.** It halts the harness with
`X-RateLimit-Reset` as the halt's expiry, so the stop lifts itself at the moment the cap does.

**A halt carries a scope.** `all` for an empty account (402) or an operator, which stop everything;
`free` for the daily cap, which stops only `:free` models. Introduced here because removing the
counter promotes the 429 from a backstop to the mechanism, and a global stop for a limit on the
*free distribution* would take a paid game down with it — a harness bound reaching a player it does
not apply to (ADR-0019).

**The tournament runner reads the halt.** It never did. It read the counter instead, so with the
counter gone a pool would have gone on scheduling games into a stop, filling its concurrency with
games the worker can only answer with `halted`.

## Alternatives considered

**Count against the full 1,000 instead of the usable 900.** The narrow fix, and it treats the
symptom: the count is an over-count by construction, so any threshold is a guess about how wrong it
is today. It would have delayed this by a week.

**Keep the counter as a warning, stop acting on it.** Attractive — the number is interesting, and
it is roughly the shape of what we spend. But a number nobody acts on is a number nobody maintains,
and it would sit in `status` inviting exactly the misreading that caused this: an operator seeing
"allowance spent" and believing it.

**Keep it for the reserve alone**, so a human can always start a game against a free model. This is
the real loss (see below) and it was weighed. Rejected because the reserve only ever protected
*starting*: a person's game against a free model can still be halted mid-play by the 429, so the
reserve bought a game that begins and then stops — which is worse than one that never starts and
says why.

## Consequences

**A human can be shut out of free play by an unattended pool.** The reserve is gone, so a pool can
consume the whole allowance and a person arriving afterwards meets a halted harness. Accepted
deliberately by the owner. It is softened by the halt being *visible and dated*: the page can say
the harness is stopped and when it lifts, where the old failure was a game that started and quietly
stopped moving.

**We learn the cap by being refused**, which costs one request. The halt then prevents the rest, so
the cost of discovery is a single call rather than the fourteen pairings a per-endpoint cooldown
spent working through the field.

**One layer of ADR-0011 is gone.** Three remain — the global daily spend switch, the per-user
quota, the per-game cap — and all three count *money*, which we compute from returned token counts
and can therefore be exact about (invariant 4). The layer removed was the only one counting
something we could not measure.

**`X-RateLimit-Reset` is now load-bearing.** If OpenRouter stops sending it the halt falls back to
the next UTC midnight, which is conservative — later than the true reset, so the failure is waiting
too long rather than resuming into a cap that has not lifted.

**Nothing reports how much of the allowance is left**, and nothing can. `status` shows the halt
when it exists and says nothing otherwise, which is honest: before, it showed a number that looked
authoritative and was not.
