# 0030. A halt pauses the board, and says so

**Status:** Accepted
**Date:** 2026-09-03
**Amends:** [0017](0017-rate-limits-pause-games.md) — a halt now takes the same `PAUSED` route a
provider rate limit does. Reinforces [0008](0008-game-events-log.md) (invariant 7) and
[0019](0019-harness-bounds-are-not-findings.md).

## Context

The global halt (OPS-19) stops every model call the system would make. Three things set it: a 402
saying our account is empty, OpenRouter's daily free-model allowance running out (OPS-20), and an
operator typing `./chessmark halt`. Of the three, the free-model cap is the one that actually
happens, and it lasts until the allowance resets — which can be most of a UTC day.

A halted turn was not run, its job was dropped, and the game was **left `RUNNING`**. That is exactly
right about the record: no turn ran, no money was spent, no model was forfeited for a bound of ours
(invariant 11). It is wrong about the page.

Nothing was written, so nothing was published, so nothing on the site changed. The header goes on
rendering a pulsing red dot and the word **live** over a board that will not move again for hours.
The conversation panel sits at the last turn. `pause_reason` is empty, because there was no pause.
There is no signal anywhere in the product that the harness has stopped — and a reader watches a
game that looks alive, waiting for a move that is not coming.

This is the same failure ADR-0017 named for provider rate limits — *"a board that stops moving with
nothing on the page to say why, which is what it did"* — surviving in the one path that did not go
through `_pause`.

## Decision

**A halt pauses every game it covers, and appends one `game_paused` event saying so.**

The status is `PAUSED`, not a new one. It is the same fact a provider pause already states, and the
whole read path is built on it: the header renders `paused` with `pause_reason` in its tooltip,
`foldEvents` turns the event into a notice in the stream, and the reconciler's resume sweep is what
puts the game back. One shape, one set of tests, no fourth branch in the panel.

**`resume_after` is the halt's own expiry, or nothing.** The free-model cap knows when it lifts —
`X-RateLimit-Reset`, or the next UTC midnight — and that timestamp becomes the game's. A credit or
operator halt does not know, and gets `None` rather than a guess: `find_resumable` reads a missing
`resume_after` as due, and the reconciler holds back any paused game the halt still covers, so it
resumes on the first tick after the halt goes. That is the earliest honest answer.

**The reconciler asks the halt per game, not once.** It used to ask `halt.active()` and return,
which was right while a halt was global and wrong the moment it had a scope: under a free-model cap
no *paid* game could be resumed or requeued either, for as long as the cap stood. `HaltState.covers`
is the one place that decides, and the two sweeps want opposite things from a game it covers — a
paused one is **held**, because resuming it would take a concurrency slot and pause again having
moved nothing; a stalled one is **requeued anyway**, because the job is no longer wasted. The worker
answers it by writing the pause above, which is precisely what a reader looking at a stalled board
needs.

**No abandonment clock.** `_pause` writes a game off after a day without a move, because a provider
hot for that long is not coming back. A halt is *ours* — our account, our allowance, our operator —
and giving up on a game over it would be a harness bound becoming a finding about a player
(ADR-0019). A halted game waits exactly as long as the halt does.

**The scope still decides who stops.** A free-model cap pauses only `:free` seats. Pausing a paid
game for a limit it is not subject to would be an outage we invented.

**One notice per pause.** The status is the flag: a game already `PAUSED` is left alone, so a
redelivered job or a requeue cannot append a second stop for a board that has not moved.

## Alternatives considered

**Leave it `RUNNING` and let the page poll `/games/{id}`.** The record would eventually say
`running` forever anyway — nothing was writing a reason. Polling would only have made the same
silence more expensive.

**A `HALTED` game status of its own.** It reads well and costs a migration, a fourth branch in
every status check, another case in the reconciler, and a second way for a game to be stopped. The
difference it would express — *why* it stopped — is already in `pause_reason` and in the event
payload's `halt_source`.

**Publish a site-wide banner instead of touching games.** Worth having, and not a substitute: a
banner cannot say which of the games in front of you are affected, and a free-scope halt affects
some and not others. It would also be the first piece of live state on the site that does not come
from the event log.

## Consequences

A halted game now looks halted: the dot stops pulsing, the header says `paused`, and the reason —
*"the harness is halted: the free-model allowance for the day is spent (429)"* — is on the page and
in the log. A replay of the game shows the stop where it happened, because it is an event like any
other.

A pause frees the concurrency slot it was holding, so a paused free game no longer counts against
its event's `max_concurrent`. Under a free-scope halt that is inert, because the tournament runner
already refuses to start anything while a halt covers it.

Every path that decides whether a turn happens now reads the same scope: the worker through
`covers`, the tournament runner through its own free-entrant check, and the reconciler through
`halted_games`. Nothing had tested the reconciler's halt behaviour at all, which is how it stayed
global for as long as it did.

The `HALTED` outcome still means *the harness is stopped* rather than *this provider is busy* — the
distinction the cooldown ladder depends on. What changed is what the game record does about it, not
how the refusal was classified.

**To watch:** a game paused by a halt and one paused by a provider are now indistinguishable at a
glance in the database. `pause_reason` is prefixed *"the harness is halted:"* and the event carries
`halt_source`, which is what an operator should filter on.
