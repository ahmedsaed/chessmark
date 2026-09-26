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

Its matchmaker balances the field: **the entrant with the fewest pairings plays first**, against
its least-met opponent, breaking ties on that opponent's pairing count and then on rating
proximity. That is a greedy incremental round robin — it covers every pair before repeating any,
and it needs no fixture list, which is what lets a pool's field change while it runs. An entrant
that joins has zero pairings and so is chosen immediately; one that just played goes to the back.

**Pairings, not settled games**, so a model whose endpoint abandons everything still takes its
turn rather than being handed the pool for ever.

The older policy — least-known entrant first, nearest-rated opponent — is still there as
`Policy.INFORMATION`. It converges ratings faster and contains no fairness term at all: it ran
`pool-free` to 44% pair coverage with one entrant on 25 pairings and another on 1
([ADR-0041](adr/0041-a-pool-balances-its-pairings.md)). Nothing exposes the choice yet; every pool
takes the default.

Either way it pairs only what it can run: scheduling ahead would write a queue against a field that
changes, and a pool never runs out of fixtures.

### A pool carries its eras

A change to the prompt or the tool surface changes what the games measure, and a pool cannot be
replaced when that happens — it never ends. So it carries the change internally: an **era** is the
prompt and tool majors joined (`v3+v4`), stamped on each pairing as it is written, and bumping
either half opens a new one on the next tick. No abandon, no new slug, no operator step
([ADR-0043](adr/0043-a-pool-carries-its-eras.md)).

Everything that decides *who plays whom* is scoped to the era being played — a new era starts from
an empty crosstable and a fresh round robin, rather than believing every pair has already met.
Concurrency and spend are not scoped: a game that is running costs an allowance whichever era
scheduled it, and it finishes and scores in its own era. The tournament page shows the current era
and offers the earlier ones.

**A decision-model event has an era of its own**: the decision harness's major (`d1`), because its
task is that harness and nothing else — a chat prompt bump says nothing about what a decision model
is asked ([ADR-0049](adr/0049-decision-models-play-through-their-own-harness.md)).

Pools are **ranked** — an unranked one would play forever and measure nothing — and a pool over paid
models is **refused without `--max-usd`**: with no end, the ceiling is the only thing that ever stops
it. Raise it with `resume --max-usd`; nothing resets on its own.

A model that leaves the catalogue is **not** auto-withdrawn from a pool. Its games are real results
and its rating is real; dropping it because an endpoint went quiet for an afternoon would rewrite
history.

## A field is one kind of model

`--decision` seats decision models and nothing else; without it an event seats chat models only.
Never both: a decision model is shown every move with facts and cannot play an illegal one, so a
field mixing the two would rank them on different tasks. They meet on the leaderboard, where every
rated game counts (ADR-0049).

```
make tournament ARGS="field --decision"
make tournament ARGS="create --name 'Deciders' --slug deciders --decision --format pool --max-usd 1"
```

The filter's `runtime` is stored with the event, so a pool re-resolves the right field every tick.

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

## How a pool chooses its next game

Worth reading once end to end, because the pieces look alike and are not. Every tick asks one
question — *how many games can start right now, and who plays them?* — and answers it in six steps
(`orchestration/tournament.py`, `_schedule_pool`):

```
1. era = current_era()                    "v3+v4" — the prompt and tool majors
   close_stale_pairings(era)              fixtures written for an older task will never be played

2. room = max_concurrent - waiting - running - due
   if room <= 0: stop                     nothing to schedule into

3. resting  = _fruitless_entrants(...)   ─┐
   resting |= _engaged_entrants(...)      ├─ "ask me again later"
   resting |= _resting_entrants(...)     ─┘

4. pairable = entrants ∩ in_field(...)    "stop asking"

5. matchmake(pairable, results, form, count=room,
             unavailable=resting, attempts=attempted)

6. record_round(games)
```

Steps 3 and 4 differ in kind. **`resting` defers an entrant; `in_field` removes one.** A model that
left the free tier is not resting and must not be shown as though it will be back on the next tick.

### The choice itself

Strip the policy indirection out of `matchmake` and `Policy.BALANCE` is two nested minima:

```python
home = min(available,           key=lambda k: (played[k], k))
away = min(available - {home},  key=lambda k: (met[{home, k}],       # least-met by home
                                               played[k],            # then furthest behind
                                               abs(rating gap),      # then closest match
                                               k))                   # then reproducible
```

Colours go to whoever is owed White, both drop out of `available`, the meeting is recorded, and the
loop runs again for the next game of the batch — so the second game sees the first. That is the
whole algorithm: **a greedy incremental round robin.** Always serve whoever is furthest behind,
against whoever they have met least. Over a field that changes with the catalogue it converges on
what a fixed schedule would give, without a schedule to invalidate when an entrant joins or leaves.

### Four counts, two dimensions

The counts are the part that gets confused, and there are only four:

| | keyed by **pair** | keyed by **entrant** |
| --- | --- | --- |
| settled games only | `results` | — |
| every attempt | `met[{a,b}]` | `played[a]` |

`results` are games that finished with a score, and feed ratings and colour balance. `attempts` are
pairings that produced no result — abandoned, or still running. `met` is the sum of both by pair;
`played` is `met` summed per entrant. All four are scoped to the current era, so bumping a prompt or
tool major resets the board.

**`met` and `played` deliberately count attempts**, which is the fix described below.

### Four filters, and the timescale each one owns

Steps 3 and 4 union three sets into one `resting`, which makes them look like one mechanism. They
are four, and **they differ on timescale** — each exists because the one above it is too
short-sighted:

| filter | asks | timescale | source |
| --- | --- | --- | --- |
| `_resting_entrants` | is this provider hot *this minute*? | 60s → hours | Redis cooldown |
| `_engaged_entrants` | does it already hold an unfinished pairing? | up to `PAUSE_WINDOW` | pairing table |
| `_fruitless_entrants` | has it failed to finish anything *lately*? | days | pairing table |
| `in_field` | is it still in the catalogue at all? | until it returns | registry |

A cooldown's first rung lapses in sixty seconds and a free shared pool stays hot for a day and a
half. An engagement block lifts the moment a pairing settles, and a model that just abandoned will
abandon the next one too. Separately from all four, the **rematch count inside `matchmake` is a
preference, not a filter** — it does not stop anyone playing, it sends a repeat fixture to the back
of the queue.

### Filtering does not unbalance the pool

Worth stating because it is the obvious worry and the answer is not obvious. **A filtered entrant
keeps its turn rather than losing it:** `played` is a cumulative count, not a rotation index, so a
model skipped this tick still has the lowest count and is chosen the moment it is available. There
is no schedule to fall out of.

Simulated over 400 ticks of a 16-model pool, a model unavailable on **half** of all ticks ends with
a pairing count identical to the field's. Only past roughly 90% unavailability does it fall
measurably behind — and a model unavailable that often is not playing anyway.

The one place filtering does leave a mark is the *opponent* choice: if the ideal opponent is
filtered out, the matchmaker substitutes the next best rather than waiting, and that meeting is
recorded. So pairs between reliable models are very slightly over-represented. It costs coverage
nothing over a pool's lifetime.

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

So the second half: an entrant whose **last two finished pairings both came to nothing** rests
(`DEAD_ATTEMPTS`, `dead_rest`). It is asked of the pairing table rather than the cooldown because
the timescales differ by two orders of magnitude — a cooldown's first rung lapses in sixty seconds,
and a free shared pool stays hot for a day and a half. Every one of those six abandoned games was
scheduled at a moment when nothing was resting.

**A rest shorter than the strike that earns it rests nobody.** `DEAD_REST` was six hours for the
whole life of the pool and never once fired. A strike cannot be earned faster than a pairing can
finish, and a dead pairing takes the full `PAUSE_WINDOW` to finish — so by the time the second
strike lands, `_engaged_entrants` has been holding that entrant for a day, measured from the same
`ended_at`. Six hours expired eighteen hours inside a block already in force. `gemma-4-26b` took
eight pairings and `glm-5.2` four while the mechanism meant to stop them was running.

`DEAD_REST` is therefore `PAUSE_WINDOW` itself — imported, not chosen, so the two cannot drift apart
— and `dead_rest(deaths)` doubles it per further strike up to a week. One number cannot describe
both *"the provider was hot yesterday afternoon"* and *"delisted in August"*: flat, it either spends
a pairing a day on a model that has not moved since summer, or puts a model having one bad day off
the board for a week.

Rested, **not withdrawn**: recovery is immediate however deep the backoff went. The run is counted
from the most recent attempt backwards and stops at the first that produced a result, so one
finished game returns an entrant to full standing on the next tick — a bad afternoon cannot quietly
remove a model from the benchmark.

## The table means different things in different formats

A **closed** event — round robin or Swiss — is ranked by **score**, then Sonneborn-Berger, then
direct encounter. Everybody plays the same schedule, and that is exactly what makes a sum of points
a ranking.

A **pool** is ranked by **Glicko-2 computed over that pool's games**, deviation as the tiebreak.
It has to be: a pool has no schedule, it pairs whoever is least known, and its entrants finish very
unequal numbers of games. In `pool-free`, entrants had completed between 0 and 10 — and two models
that had won *every* game they played stood third and fourth behind one that had lost a game in
eight. Points there partly measure how many games a model was handed.

Points and W/D/L stay on the page. They are what lets a reader check the rating against something;
they just stop deciding the order.

Three things worth knowing about that rating ([ADR-0027](adr/0027-a-pool-is-ranked-by-its-own-rating.md)):

- **It is that pool's, not the platform's.** A place here cannot move because of a game played in
  another event. It will therefore disagree with the leaderboard, which is correct — they were
  computed over different games.
- **The eligibility rules are identical.** The scope is a `where` clause on which games are read,
  not a second set of rules, so a game the leaderboard excludes is excluded here too.
- **An entrant with no ratable game reads `unrated` and sorts last**, never 1500. An unmeasured
  model is not an average one, and seating it mid-table would make exactly the claim the rating
  deviation exists to avoid.

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
