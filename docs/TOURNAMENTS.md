# Tournaments

A tournament is a **format, a field, and a set of bounds**. Phase 13.

The field is a `FieldFilter`, never a list, so every bracket is the same machinery: `--free`,
`--open-weights`, `--provider anthropic`, `--max-credits 1` all resolve through one query.

```
make tournament ARGS="field --free"                 # who would enter; costs nothing
make tournament ARGS="create --name '…' --slug x --free --format pool"   # never ends
make tournament ARGS="create --name '…' --slug y --free --format swiss --rounds 5"
make worker                                          # exactly one; run schedules, it does not play
make tournament ARGS="run x"                         # ticks until finished or paused
make tournament ARGS="pause x --abort-live"          # stop; --abort-live frees queued jobs
make tournament ARGS="resume x --max-usd 5"          # raise the ceiling that stopped it
make tournament ARGS="withdraw x vendor/model:free"
make tournament ARGS="standings x"
```

On a server, `./chessmark tournament <subcommand>` and `./chessmark standings <slug>`.

## A pool is the open case

`--format pool` never ends, re-resolves its field every tick so a newly listed model joins by
itself, and ranks by Glicko-2 rather than points — which is what makes an open population rankable
at all.

Its matchmaker follows what the rating actually measures: **the least-known entrant plays first**,
against the nearest-rated opponent who is not a rematch. It pairs only what it can run, because
scheduling ahead would freeze that information at the moment it was written, and a pool never runs
out of fixtures.

Pools are **ranked** — an unranked one would play forever and measure nothing — and a pool over paid
models is **refused without `--max-usd`**: with no end, the ceiling is the only thing that ever stops
it. Raise it with `resume --max-usd`; nothing resets on its own.

A model that leaves the catalogue is **not** auto-withdrawn from a pool. Its games are real results
and its rating is real; dropping it because an endpoint went quiet for an afternoon would rewrite
history.

## A closed event's field is frozen when it is created

`resolve_field` runs once and writes `tournament_entrants`. A model registered afterwards does not
join, and one that disappears upstream does not leave.

That is deliberate: a round robin schedules its whole fixture list from the field, so admitting a
latecomer would invalidate it, and a table whose rows played different opponents means different
things per row. To change a running field, `withdraw` an entrant or create a second event.

## Withdrawal, and what happens to the unplayed pairings

A model that becomes unplayable mid-event is **not** handled gracefully by itself: its games are
attempted, fail at ply 0, and are marked *abandoned* — honest, but it wastes pairings.

`withdraw` is the deliberate path, and it **abandons** the unplayed pairings rather than awarding
them, because a walkover is not a finding about the opponent. `./chessmark prune --model <slug>
--only-named --apply` does the same for a model the catalogue cannot predict is unplayable
(ADR-0019).

## Resuming

`advance` holds **no state between calls**, which is the whole of the resume criterion: a restart
asks the table what has been played rather than trusting a dead process.

A **paused** event returns early from `advance`. Without that it restarts on the next tick —
including one its own budget stopped, which would then spend past the ceiling it had just halted at.

## A pairing's state is its game's

`white_score` and `abandoned_reason` are **verdicts already written**, and resuming a game
invalidates whichever one it holds. The game record is the authority (invariant 1), so that is the
direction every disagreement resolves.

`settle` **reconciles rather than records**: a finished game's result overwrites a wrong score and
clears a stale abandonment, a game in flight clears a score it should not have, and a pairing that
already agrees is left alone. `_settle_finished` therefore offers it every pairing with a game on
every tick, which is a few dozen reads and makes the disagreement impossible to sustain.

This was not theoretical. Both halves reached one page at once: four pairings kept the score of a
forfeit that had just been overturned, so games running at up to ply 89 were drawn as *played* and
the event reported `live: 0` while four boards moved; and a game abandoned on a provider 404, resumed
and played to checkmate at ply 120, was still drawn as *abandoned* and its real result had nowhere to
go. A stale score does not merely mis-draw a square — it blocks the true result, and the pool's
leader was half a point better off than it had earned.

`_state` reads the game's status wherever there is a game, and falls back to the pairing's own
columns only for a pairing that has none.

## The matchmaker and unavailability

The pool must not pair a model that is resting, and **must not pair one that already has a paused
game**. The cooldown alone left a gap: its first rung is sixty seconds, so it lapses, the matchmaker
sees the model as available, pairs it, and it is refused again — four paused games against one model
with nothing running.

Asking about paused games is the precise fix. A *ceiling* on paused games was the imprecise one, and
was reverted: it stalled the pool completely, and the failure it guarded against is already prevented
by the cooldown. See [ADR-0017](adr/0017-rate-limits-pause-games.md).

### A pairing that produced nothing is still a rematch

An abandoned pairing carries **no score**, deliberately — it must never be scored, because a
provider we could not reach is our failure and not a finding (invariant 11). The matchmaker used to
read the same absence as *"these two have never met"*, and those are different statements.

The result was a lock. `gemma-4-26b` and `gemma-4-31b` were both unrated and both served only by the
same rate-limited Google pool, so each was the entrant least known about and each was the other's
nearest unmet opponent. They were scheduled **seven times over five days and never made a move**:
every game paused until the 24-hour patience ran out, was abandoned, recorded nothing, and was
chosen again within the hour.

So `matchmake` now takes `attempts` — every pairing written down that produced no result — alongside
`results`, and counts both as meetings. `db.tournaments.attempted` supplies them; `results_so_far`
is untouched, because scoring and rematch-avoidance are different questions of the same rows.

**That half alone would make the event worse.** It stops one fixture repeating; it does not stop a
model that cannot play, which is still permanently the least-known entrant and now goes looking for
a *fresh* opponent each time. Both seats of a paused game are parked while it waits, so every
attempt would take a healthy entrant out of the pool for a day with it — seven dead games wasting
two models that were failing anyway becomes thirteen wasting the field.

So the second half: an entrant whose **last two finished pairings both came to nothing** rests for
six hours (`DEAD_ATTEMPTS`, `DEAD_REST`). It is asked of the pairing table rather than the cooldown
because the timescales differ by two orders of magnitude — a cooldown's first rung lapses in sixty
seconds, and a free shared pool stays hot for a day and a half. Every one of those six abandoned
games was scheduled at a moment when nothing was resting.

Rested, **not withdrawn**: the span is measured from the last dead attempt and lapses on its own, so
a bad afternoon cannot quietly remove a model from the benchmark.

## Bounds

The **ply cap is a cost bound, not a rules bound.** Games terminate on their own because the hard
draw backstops always apply — a fivefold repetition, and seventy-five moves without progress
([ADR-0020](adr/0020-claimable-draws.md)). 300 plies is the standard; 80 sat at the median of real
games and let the harness decide half the results.

Expect **longer games** than before ADR-0020: a threefold repetition no longer ends a game unless a
player claims it.

A **harness bound is never a finding about a player**. See
[ADR-0019](adr/0019-harness-bounds-are-not-findings.md).

### Concurrency, and the one setting worth changing later

`max_concurrent` is how many of an event's games may run at once, and it is the only thing about a
running event that `set` will change:

```
./chessmark tournament set pool-free --max-concurrent 4
./chessmark tournament set pool-free            # what it is now
```

Everything else `create` fixes is either a statement about what is being measured — format, field,
ranked — which must not drift under an event that is halfway through, or it already has its own
command (`resume --max-usd` raises a budget that stopped one). Concurrency is different because its
right value is not knowable when the event is created: it depends on how many workers are up, how
hot the free pools are today, and how long a turn is taking.

It takes effect on the runner's next tick and nothing needs restarting. Lowering it below what is
already in flight stops nothing — the runner and the reconciler simply start nothing new until the
count falls, because a game in progress outranks a bound changed after it began
([ADR-0025](adr/0025-finishing-a-game-beats-starting-one.md)).

**Raising it without raising `WORKER_REPLICAS` mostly buys nothing.** A worker plays one turn at a
time, start to finish, so extra slots against one worker lengthen the queue it is already working
through rather than shortening the wall clock. `./chessmark workers 3` is the other half.

What a pool with `max_concurrent = 1` costs is not obvious and was measured: every rate limit
stalls the whole event, and a game whose pause expires waits for the single running game before it
can move. Paused games hold no slot ([ADR-0017](adr/0017-rate-limits-pause-games.md)) and the
matchmaker already skips resting providers, so the bound is doing less work here than it looks.
