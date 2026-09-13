# 0041. A pool balances its pairings, and does it without a schedule

**Status:** Accepted
**Date:** 2026-09-13
**Amends:** the pool policy in `tournament/matchmaking.py`, which ADR-0027 and OPS-13/OPS-21/OPS-22
built up one correction at a time. Those corrections stand; what changes is the objective they were
correcting.

## Context

`pool-free` had run for eighteen days: 19 entrants, 123 pairings, 85 settled. Measured on
**pairings** — which is the right measure, because a model that abandons every game has still had
its turn — the field looks like this:

```
pairings per entrant   min 1   max 25   mean 12.8   σ 7.3
pair coverage          76 / 171  =  44%
pair histogram         51 pairs once · 13 twice · 6 thrice · 5 four times · 1 seven times
```

More than half the fixtures in the tournament have never been attempted, while one pair has been
attempted seven times. Four entrants have one or two pairings; `dots-3-note-preview` has 25.

The visible cost is the top of the board. `laguna-s-2.1` sits first on **9 games, 9 wins, RD 245**,
having never been paired with the second, fifth, sixth or ninth place. Its rank is a statement
about who it happened to meet.

### The policy has no fairness term, and was never meant to

`matchmake` picks the entrant with the **highest rating deviation**, then the **nearest-rated**
opponent who is not a rematch. That is the right question for a young pool — it converges ratings
fastest, and it is what makes a newly listed model settle instead of sitting unrated. It contains
nothing at all about how often anyone has played. The imbalance is not a bug in it; it is the
objective.

### And its one counter-weight was dead code

```python
home = min(available, key=lambda key: (-known[key].deviation, known[key].games, key))
```

`Form.games` is the second key. `_form()` in `orchestration/tournament.py` builds every `Form` from
a rating and a deviation and **never sets `games`**, so it was `0` for every entrant for the whole
life of the pool. Ties among equally-unknown entrants have been breaking *alphabetically*.

### A schedule is the obvious fix and the wrong one

`round_robin()` already exists — circle method, phantom for an odd field, colour repair — and gives
exact balance and total coverage by construction. It cannot be used here, because **a pool is not a
tournament**: its field tracks the catalogue, entrants join when a free model is listed and leave
when one is withdrawn, and a fixture list written on Monday describes a field that no longer exists
by Friday. That difference is what separates the two formats.

## Decision

**The pool gets a second policy, and it is the default.**

```python
class Policy(StrEnum):
    BALANCE = "balance"           # fewest pairings first, then the least-met opponent
    INFORMATION = "information"   # highest deviation first, then the nearest rating
```

`BALANCE` is a **greedy incremental round robin**: at each choice, take the entrant with the fewest
pairings, then its least-met opponent, breaking ties on that opponent's pairing count and finally
on rating proximity. No schedule is written, and over a changing field it converges on the coverage
a schedule would have given.

Three properties fall out of that one rule, and they are the three things a pool needs:

* **It self-interleaves.** An entrant that just played has one more pairing than everybody else, so
  it goes to the back. There is no separate "don't repeat" mechanism; the count is the mechanism.
* **A newcomer catches up by itself.** Zero pairings is top priority, so it is chosen immediately
  and keeps being chosen until it is level. Nothing has to be regenerated when the field changes.
* **A model that never finishes a game still takes its turn**, because the count is of *pairings*,
  not of settled games. This is deliberate and it is the whole reason for the distinction:
  balancing on results would send the pool back to whoever abandons, for ever.

**`Form.games` is deleted rather than populated.** How many times a pair has been put on the board
is already known from `_meetings`; deriving the count from that keeps one source instead of two
that can disagree.

**Rating proximity survives as the last tie-break**, where it costs nothing: among opponents equally
unmet and equally under-played, the nearer-rated game is still the better one.

**The two policies share one driver.** Batching, colours, round numbers and availability are
unchanged and untouched; a policy is two comparison functions and nothing else. That is what makes
this a refactor rather than a rewrite, and what makes `INFORMATION` cheap to keep.

### Measured, not asserted

Simulated over 19 entrants with four of them servable ~12% of ticks (the gemma/glm case), three
newcomers joining a third of the way through, 800 pairings:

| | pairings/entrant | coverage | busiest entrant |
| --- | --- | ---: | ---: |
| `INFORMATION` | min 38, max **279**, σ 60.8 | 68% | 35% of all games |
| `BALANCE` | min 38, max **79**, σ 12.3 | **100%** | 12% of all games |

The residual σ under `BALANCE` is entirely the four hard-to-serve entrants. That part is
irreducible while we pair only what is available — and it self-corrects, because being behind is
what makes an entrant next.

## Alternatives considered

**A fixed round-robin fixture list.** Exact balance, total coverage, and it is already implemented.
Rejected because the field is not fixed: every join or withdrawal invalidates the schedule, and the
choice between regenerating (which discards history) and appending (which is the greedy rule with
extra steps) is not a choice.

**Keep `INFORMATION` and add a fairness penalty to it.** A weighted sum of deviation and play count
needs a constant relating one to the other, and `_REMATCH_PENALTY = 100_000` is what that looks like
when you want one term to dominate. Two named policies say which question is being asked; a tuned
constant hides it.

**Balance on settled games rather than pairings.** Ranks a model that abandons as under-played for
ever, which is exactly the loop OPS-21 was written to break.

**Decouple pairing from availability entirely** — decide the fixtures, then run them whenever both
seats are free. Attractive, and it is what a real league does. It needs a fixture list to decide
*from*, so it reduces to the fixed-schedule option above.

## Consequences

**Ratings will converge more slowly, and be worth more when they do.** `INFORMATION` spent its
games where they moved a rating most; `BALANCE` spends some of them on mismatches. Glicko-2 handles
a mismatch correctly — it simply updates less — and a leaderboard that has to compare everybody
needs coverage more than it needs fast convergence on a few.

**Expect more lopsided games on the site.** A 1975 against a 787 is now a fixture that will happen,
where before it was avoided. That is the honest consequence of covering the field.

**`INFORMATION` stays selectable and tested.** A new pool, where most entrants are unrated and the
first question really is "who is nobody sure about", may want it. It is no longer the default and
no longer the only thing the module can express.

**Nothing is exposed to configure it yet.** `matchmake` takes a `policy`; `Tournament` has no column
for one, so every pool uses the default. Adding the column is a migration and a settings surface,
and it should wait until there is a second pool that wants a different answer.
