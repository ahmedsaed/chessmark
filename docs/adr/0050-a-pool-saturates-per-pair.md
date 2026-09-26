# 0050. A pool saturates per pair

**Status:** Accepted
**Date:** 2026-09-26
**Amends:** [0041](0041-a-pool-balances-its-pairings.md): the balance policy chooses *who* plays next,
and this decides *whether* anyone does.

## Context

A pool never ends. That is what lets it seat models as they are listed, and it is also why the
only thing that ever stopped one was its budget running out, a pause, or its active hours.
`pool-free` kept pairing models that had already met many times, spending its scarce free-tier
allowance on the twentieth game between a pair while a newly listed model waited its turn behind
them.

The decision models (ADR-0049) made the gap concrete. The owner wanted an event where the two
current decision models play each other and then stop, and where any decision model listed later
joins and meets the others, like the free pool. A closed round robin stops, but its field is
frozen. A pool admits newcomers, but it never stops.

Real events handle this in a few ways:

| Format | How it stops | Newcomers |
| --- | --- | --- |
| Round robin, Swiss | fixed rounds | no |
| Online arenas | a fixed duration | until time runs out |
| Engine rating lists (CCRL, CEGT) | each new engine plays a fixed gauntlet against the established ones | yes, which is the point of them |
| Rating pools | when a rating is confident enough | yes |

The engine rating lists match the need exactly. The list never closes, each newcomer plays a known
number of games, and the list goes quiet until the next newcomer arrives.

## Decision

**A pool may have a per-pair target, `games_per_pair`.** A pair that has had that many games in
the current era is not paired again. When every pair has, the pool is **saturated**: it starts
nothing, stays `running`, and is idle rather than finished. A newcomer admitted on a later tick
brings pairs that have not met, and the pool wakes and plays only those. The existing entrants
never replay each other.

* **Every pairing that was not abandoned counts:** settled, running, paused or waiting. Unsettled
  ones count because they will settle, so a tick cannot schedule past the target while the first
  games are still being played. Abandoned ones don't count: an abandoned pairing produced no result
  (invariant 11), and a target of two games means two games that were actually decided. Resting a
  model that keeps producing nothing is already `_fruitless_entrants`' job, so a dead model cannot
  turn this into a loop.
* **Per era.** A new era starts an empty crosstable (ADR-0043), so bumping a version replays every
  pair up to the target, because the task changed.
* **Colours balance within the pair.** The matchmaker already balances who has had White against
  whom, so a target of two is one game with each colour.
* **`None` is today's pool.** Every existing event keeps its current behaviour until an operator
  sets a target (`tournament set <slug> --games-per-pair N`, where `0` clears it). The budget,
  active hours and pause still apply, and whichever limit is reached first stops play.
* **Pools only.** A closed event's schedule is written from its field at creation, so a target on
  one would be a number nothing reads, and it is refused.
* **The page says so.** A saturated pool shows that every pair has played its games and it is
  waiting for a new model, because a pool that simply stops starting games looks broken.

## Alternatives considered

* **Stop an entrant when its rating is confident (a deviation threshold).** Cheaper in games, but
  opaque: a reader cannot tell why two models never met. Worth adding later beside the target, not
  instead of it.
* **A fixed duration.** It measures the calendar, not the models.
* **Ending the pool when it saturates.** A finished event cannot take a newcomer, and taking
  newcomers is the reason for a pool.

## Consequences

* A pool with a target costs a known amount per newcomer: target × current field size games.
* A saturated pool still ticks, cheaply: every tick asks whether the field has changed.
* `pool-free` stops spending on well-met pairs as soon as it is given a target. How much history it
  already has decides how long it stays idle: pairs already past the target are simply not paired
  again, and nothing is undone.
