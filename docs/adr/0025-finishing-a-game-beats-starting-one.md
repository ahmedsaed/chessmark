# 0025. Finishing a game beats starting one

**Status:** Accepted
**Date:** 2026-09-02
**Amends** [0017](0017-rate-limits-pause-games.md) — a pause frees the concurrency slot, and that
was right; what was missing is that the slot has to come back before a new pairing takes it.
Implements OPS-22.

## Context

`pool-free` abandoned **21 of 56 pairings**. Eleven of them had played real chess — 92 plies, 71,
68, 56 — and the assumption was that free-tier allowances had starved them. Measuring the pauses
said otherwise.

For the five deepest abandoned games, the time the *provider* asked them to wait against the time
they actually waited:

| game | plies | provider asked | actually waited | ours |
| --- | --- | --- | --- | --- |
| `0fbe4bae` | 92 | 6.9h | 20.4h | 66% |
| `7896571f` | 71 | **0.4h** | 26.7h | **99%** |
| `a016a326` | 68 | **0.15h** | 20.4h | **99%** |
| `ab385e1d` | 56 | 11.9h | 25.1h | 53% |
| `b5546c1b` | 34 | 11.9h | 23.0h | 48% |

**Between 48% and 99% of every pause was our own queue.** One game was told to come back in sixty
seconds and sat for 16.4 hours. Two were asked, across their whole lives, for nine minutes and
twenty-four minutes.

Two independent defects produced that, and each on its own is enough to lose a game.

**Nothing counted a game that was ready to move.** `tournament._start_games` bounded itself on
`repo.in_flight` — `PENDING` or `RUNNING` — while `reconciler.with_room_to_run` resumed paused
games against the same `max_concurrent`. A paused game whose `resume_after` had passed was
invisible to the first and visible to the second, so the two raced for one slot on whichever ticked
first. The runner ticks on its own container; a reconciler runs on every worker. With
`max_concurrent = 1` and turns that run for minutes, losing that race cost a full cycle, every
cycle.

**And the patience window measured the wrong thing.** `PAUSE_WINDOW` ran from the earliest
`GAME_PAUSED` event and nothing ever reset it, so it asked *"has this game been pausing on and off
for a day?"* while its own comment claimed *"cannot get a turn in twenty-four hours"*. A game that
paused at ply 4 and then played eighty-eight more moves was abandoned anyway. The three games above
at plies 71, 68 and 56 had never once gone more than 17.4 hours without moving, and all three were
within seven **playing**-hours of a real result.

The two compounded: our own queueing spent the window, and the window could not tell that the game
was using the time to play chess.

## Decision

**A game due to resume reserves its concurrency slot.** `repo.due_to_resume` returns the pairings
whose game is `PAUSED` with `resume_after` in the past (or absent, for the same reason
`find_resumable` includes it), and both `_start_games` and `_pair_more` subtract it from
`max_concurrent`. The runner then starts nothing, the reconciler fills the slot on its next sweep,
and the game that was ready to move moves.

The principle, stated plainly because it will come up again: **a game in progress outranks a new
one.** Its position is real, its allowance is already spent, and an abandoned game at ply 71 is
worth less than the pairing that displaced it.

**Patience is measured from the last move.** `PAUSE_WINDOW` now runs from the most recent
`MOVE_MADE` event, falling back to when the game started so a pairing that never reaches ply 1 is
on the same clock as one that does. A game that keeps moving never ages out; a game that is truly
dark still dies in a day with nothing to show for it.

The pause *count* stays in the log line and the event payload, and decides nothing. It was the
policy once, then the anchor; both made the harness's patience a function of how the cooldown
ladder happened to be tuned.

## Alternatives considered

**Lengthen `PAUSE_WINDOW`.** The obvious move and the wrong one: it buys every stuck pairing the
same extra hours it buys a healthy one, and the two games asked for nine and twenty-four minutes
would still have been abandoned — just later. It treats a wrong measurement by making it bigger.

**Raise `max_concurrent`.** Worth doing and orthogonal. It reduces how often the race is lost
without changing who wins it, and a pool of one would still starve its own paused games. This is a
configuration knob, not a decision.

**Let the reconciler start games too, so one component owns the slot.** Tempting, since the
duplication is the root of the race. Rejected: the reconciler exists to rescue games and knows
nothing about fields, ratings or matchmaking, and moving pairing into it would put the event's
policy in the module that runs on every worker.

## Consequences

**A pool with `max_concurrent = 1` and one due game starts nothing until the reconciler runs.** By
design — that is the slot being held for its owner — but it means the runner's `started` can be
zero for a tick with entrants available, and `status` should not read that as a stall.

**`due_to_resume` and `find_resumable` must agree on what "due" means**, including the
`resume_after IS NULL` case. They are two queries over one question; if they drift, the runner
reserves a slot the reconciler will not fill and the pool holds forever. Tested on both sides.

**Two existing tests changed meaning.** `test_a_paused_game_does_not_hold_the_concurrency_slot` and
`test_a_pool_starts_another_game_while_others_are_paused` paused games without a `resume_after`,
which now reads as *due*. They set a real future time, which is what the worker always writes — so
they now assert what they always claimed to: a game waiting on a **provider** does not hold the
slot.

**A game can now outlive a day, and there is no absolute ceiling on one.** A pairing that moves
once every twenty-three hours would run indefinitely. `max_plies` (300) and the event's budget
bound it, and the concurrency slot is only held while it is actually moving, so the exposure is a
long-lived record rather than runaway spending. Watched rather than pre-empted.
