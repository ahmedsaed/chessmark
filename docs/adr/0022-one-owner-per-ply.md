# 0022. A ply has one owner, and a game that ended stays ended

**Status:** Accepted
**Date:** 2026-08-30
**Amends:** [0007](0007-turn-level-jobs.md) — `expected_ply` makes *redelivery* safe, which is not
the same as making *concurrency* safe, and the difference cost a rated result.

## Context

Game `29e7f004` appended **seven `game_ended` rows**. A game that has ended should append one.

Three separate causes hid behind that one symptom, and only the third is still live:

* Two `timeout` endings at ply 8 came from the 600-second per-turn clock, removed in `12f428f`.
* Two `game_resumed` events with a `previous_termination` came from `scripts/resume_game.py` — an
  operator resuming a game by hand, working as intended.
* Three `truncated` endings at ply 19 came from **two workers playing the same ply at the same
  time**, and then from the loser of that race resurrecting the game the winner had finished.

### `expected_ply` protects redelivery, not concurrency

ADR-0007's idempotency check compares the job's `expected_ply` against the game's real state and
drops the job if they disagree. That is exactly right for a redelivered message: the first attempt
either committed, in which case the ply has moved on and the duplicate is dropped, or it did not, in
which case rerunning it is correct.

It offers nothing against two jobs running *simultaneously*. Both read ply 18, both find it matches,
both play ply 19. The event log shows it plainly — `turn_started` at ply 19 twice, **fifty
milliseconds apart**.

### The reconciler was manufacturing the duplicates

`DEFAULT_STALE_AFTER` is 20 minutes. Since `12f428f` a single provider call may legitimately take
ten, and `max_tool_iterations` allows twenty calls in a turn. **A healthy slow turn now looks
stalled**, so `find_stalled` enqueues a second job for a ply that is still being played, and the
reconciler becomes the source of the race it exists to fix.

### The loser of the race un-finished the game

`_advance` re-reads the game and returns `NOT_RUNNING` when its status is no longer `RUNNING`, so a
*late* job is harmless. But the other terminal-writing paths do not ask:

* `_pause` sets `game.status = PAUSED` unconditionally (`worker.py:408`).
* `_abandon` sets `ABORTED` unconditionally.
* `_conclude` guards on `FINISHED` and not on `ABORTED`.

So the second worker's turn, finishing minutes after the first had concluded the game, wrote
`PAUSED` over a finished record. The reconciler then did its job, resumed it, and the game played
ply 19 for a third time.

### It changed a rating

Game `855e208d` ended twice. First as `budget_exceeded` — a harness termination, correctly excluded
from ratings. Then, after being resurrected, as `error_forfeit` — which **is** rated, and which is
the verdict that stands today, `1-0` against `nvidia/nemotron-3-super-120b-a12b`.

The same model failure produced two different classifications, and a race decided which one reached
the leaderboard. Whatever the right answer is, it must not be chosen by scheduling.

## Decision

**One worker owns a ply, enforced by the database.**

`_advance` takes a row lock on the game — `SELECT … FOR UPDATE NOWAIT` — before doing anything else.
The turn already runs inside a single transaction spanning its provider calls, so holding the lock
for the turn's duration changes nothing about how long a row is held.

`NOWAIT` rather than a plain wait, and the distinction is the whole point: a second worker must
learn *immediately* that someone else owns this ply and drop its job, not block a connection for the
hours a legitimate turn may take. The lock failure is a normal outcome with its own name
(`IN_FLIGHT`), not an error.

It does not re-enqueue. The owner will enqueue the next ply when it commits, and if the owner dies
the queue's `XAUTOCLAIM` and the reconciler both still cover it.

**A terminal game stays terminal.** `_pause`, `_abandon` and `_disable_gated` re-read the game's
status inside their own transaction and do nothing if it is already over. `_conclude` guards
`ABORTED` as well as `FINISHED`. A game is un-ended by exactly one thing: an operator running
`scripts/resume_game.py`, which says so in the event it writes.

**The stale threshold stops manufacturing duplicates.** `DEFAULT_STALE_AFTER` rises to 45 minutes.
This is tuning, not the guarantee — the lock is the guarantee — and it is deliberately below the
worst legitimate turn, because a duplicate is now dropped in microseconds and the alternative is a
genuinely stalled game waiting hours for rescue.

## Alternatives considered

**Track in-flight games and skip them in `find_stalled`.** A second source of truth about what is
running, which is the thing ADR-0008 says Postgres already answers. It would also be wrong precisely
when it matters — after a crash, when the tracker says "running" and nothing is.

**A heartbeat written per round-trip, with the reconciler reading its age.** Accurate, and it turns
every provider call into an extra write and invents a liveness protocol beside the one the queue
already implements. The lock gives a stronger guarantee for less.

**Raise `DEFAULT_STALE_AFTER` above the worst possible turn (3h20m) and rely on that alone.** No
lock, no new failure mode, and it makes a genuinely stalled game invisible for three hours — while
still racing whenever a turn exceeds whatever number is chosen. It treats a correctness bug as a
tuning problem.

**A Redis lock per game.** We hold one for the reconciler sweep already (`SingleFlight`), so it is
familiar. But the thing being protected is a Postgres row and its transaction, and a lock in a
different datastore can be held by a worker whose transaction has already rolled back. The row lock
cannot disagree with the row.

## Consequences

**A worker can now decline a job it was handed**, which is a new outcome to reason about in logs and
tests. `IN_FLIGHT` is not a failure and must not be counted as one.

**A turn holds a row lock for as long as it runs** — up to twenty calls at ten minutes each in the
worst case. Nothing else writes that row during a turn, so this blocks nothing that was not already
serialised by the transaction; but a long-held lock is worth knowing about when reading a `pg_locks`
dump.

**The three duplicate `truncated` forfeits in `29e7f004`'s log stay in the log.** It is append-only
(ADR-0008) and the game record is the authority where they disagree (invariant 1), so ratings are
already correct. What was wrong was the reading, and the fix is that no further game writes them.

**`855e208d` needs re-settling by hand.** Its stored verdict was chosen by a race, and no rule can
recover which ending "should" have won — both really happened. The operator decides; the game record
is what gets corrected, and the leaderboard follows it.
