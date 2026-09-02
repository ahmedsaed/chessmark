# 0027. A pool is ranked by its own rating

**Status:** Accepted
**Date:** 2026-09-02
**Amends** [0015](0015-quantization-as-identity-and-pinned-endpoints.md) — the rated thing is
`(model, quantization)`, and a tournament's entrants are keyed by model alone; this says how the two
join. Implements BENCH-11.

## Context

The platform had two ways of ordering models and they were never reconciled, because until there was
a pool they never had to be.

`tournament/standings.py` is a **chess tournament table**: score, then Sonneborn-Berger, then direct
encounter. That is correct and conventional for a closed event. A round robin or a Swiss hands every
entrant the same number of games, and that is precisely what makes a *sum* of points a ranking.

`bench/` is **Glicko-2** across every ranked game: a rating with a deviation beside it, answering
how strong a model is rather than what it did in one event.

A **pool** breaks the assumption the first one rests on. It runs indefinitely, its field changes as
the catalogue does, and it pairs whoever is least known rather than following a schedule. In the
live `pool-free`, entrants had completed between **0 and 10** games. A sum over that measures how
many games a model was handed as much as how it played:

| | played | score | placed |
| --- | --- | --- | --- |
| `minimax-m2.7` | 8 | 6.0 | **1st** |
| `dots-3-note-preview` | 5 | **5.0 from 5** | 3rd |
| `laguna-s-2.1` | 5 | **5.0 from 5** | 4th |

Two models that won every game they played, ranked below one that lost a game. Sonneborn-Berger does
not rescue it — it is another sum, so it also rewards volume.

The obvious repair is to show the leaderboard's rating on the event page. The owner rejected it, and
on the right grounds: a model's place in *this* event would then move because of a game played in a
different one. An event table has to be a statement about that event.

## Decision

**The format decides what the table means.**

- A **closed** event — round robin, Swiss — is ordered by score, Sonneborn-Berger and direct
  encounter, exactly as before. Nothing changes.
- A **pool** is ordered by **Glicko-2 computed over that pool's games alone**, deviation as the
  tiebreak. Points and W/D/L stay in the payload and on the page, because they are facts a reader
  needs in order to check the rating against anything — they simply stop deciding the order.

**The eligibility rules do not change with the scope.** `bench.service` grew a `tournament_id` that
is a `where` clause on which games are read and nothing else: the same `ratable.judge`, the same
Glicko-2, the same daily rating periods. A game the leaderboard excludes is excluded from the pool
table too, so the two can differ in what they were computed over and never in what counts.

**An unrated entrant sorts last, and prints "unrated".** Not 1500. Defaulting an unmeasured model to
the mean would seat it above every model measured below the mean, which is exactly the claim the
rating deviation exists to stop us making. Three of `pool-free`'s entrants had never completed a
game.

**A slug with two contestants takes the one with more games.** The leaderboard is keyed by `(model,
quantization)` (ADR-0015) and a tournament's entrants by model slug. Within one event they almost
always coincide, because a seat is pinned at creation and plays every game on one endpoint. A
registry change mid-event can break that, and then a slug has two ratings, neither wrong; the one
backed by more games is the one the reader is looking at. Averaging them would invent a number no
game produced.

## Alternatives considered

**Show the global rating on the event page.** One number, one meaning, no new code — and rejected by
the owner because a place in this event would move for reasons outside it. A table that cannot be
checked against the games above it is not an event table.

**Leave points as they are.** Defensible: score and Sonneborn-Berger are conventional, need no
explaining, and are a plain record of what happened. Rejected because the ordering is misleading
exactly when a pool is doing its job — pairing the least-known model, which is the one with fewest
games.

**Rate from `Result` inside `tournament/standings.py`**, keeping everything pure and importing only
`bench.glicko2`. Attractive, and wrong in the way that matters: `results_so_far` is settled pairings,
which is *close* to the ratable set and not equal to it — it does not know about endpoint drift,
floating aliases, or a non-current prompt version. The pool table would then have counted games the
leaderboard threw away, which is the incoherence this whole decision is about avoiding.

**Normalise points to a score rate.** Cheap, and it fixes the ordering above. But a rate says nothing
about *whom* a model beat, so a model that went 5/5 against the bottom of the field would still lead
one that went 6/8 against the top. Glicko-2 already weighs the opponent, and weighs it by how well
known that opponent is.

## Consequences

**The pool table is now recomputed rather than summed**, so rendering an event page costs a full
Glicko-2 run over that event's games. It is milliseconds at the current scale and it is the same
arithmetic the leaderboard already does per request, but it is no longer true that the table is
derivable from `results` alone — the module's own opening claim. `standings()` stays pure by being
*handed* the ratings, so the property survives where it is testable.

**Two numbers now describe one model, and they will disagree.** A model can lead a pool and sit
mid-leaderboard, which is correct and will still be asked about. The column carries a title saying
what it was computed over; the methodology page (BENCH-10) is where the fuller answer belongs.

**A pool's rating moves when a *game* is reopened.** `resume` makes an abandoned game ratable again,
which changes the local rating and can reorder the table — the same property the leaderboard already
has, now visible in a second place.
