# 0028. A wider prior, and a rating that says when it is not settled

**Status:** Accepted
**Date:** 2026-09-02
**Amends** [0027](0027-a-pool-is-ranked-by-its-own-rating.md) — it made a rating the thing a pool
table is ordered by, which raises the question of how quickly that rating becomes worth ordering by,
and how a reader is told when it is not. Implements BENCH-12.

## Context

Two numbers in `bench/glicko2.py` were Glickman's defaults, taken without being asked whether they
suited this population. Comparing them with Lichess — the largest public Glicko-2 deployment, and
one that publishes its constants — made both worth revisiting.

**The initial deviation was 350.** That figure is calibrated for a rating pool where a new player is
a rare event among many settled ones, so a wide prior would let one lucky result throw a stable
table around. Ours is the opposite population by construction: `tournament/matchmaking.py` picks the
entrant with the **largest** deviation, on the reasoning that their next game is worth the most. The
pool therefore spends most of its games on models that have barely played, and a prior tuned for the
opposite case makes those games count for less than they are worth. Lichess uses 500.

**Nothing said when a rating was not yet worth reading.** The leaderboard prints `1650 ± 208`, which
is honest and which most readers cannot act on. Meanwhile the whole live field sat between ± 150 and
± 265 over two to nine games each — that is, *nothing on the page was settled*, and the page had no
way of saying so. Lichess marks a rating provisional with a `?` while RD > 110.

## Decision

**`DEFAULT_RD` is 500.** A prior, not a licence: the same evidence still produces the same ordering,
and the deviation is published beside the rating so a fast-moving early number cannot be read as a
settled one.

**A rating above `PROVISIONAL_RD` (110) is provisional and says so**, as a `?` beside the number on
the leaderboard and on a pool's table, with the deviation kept in the tooltip and in the payload for
readers who do think in deviations.

**110 is Lichess's number, adopted verbatim.** A threshold chosen to make our own table look settled
would be worth nothing, and this one does the opposite: on today's data **every contestant is
provisional**. That is the correct thing for the page to say — nine games do not settle a rating —
and the flag begins to discriminate at roughly fifteen to twenty games, which a pool reaches on its
own.

**`provisional` is derived, never stored**, so it cannot drift from the deviation it describes.
`tournament/standings.py` is *handed* the flag rather than computing it, for the same reason it is
handed the rating: the threshold belongs to the rating system, and that module stays free of
`bench`.

**Provisional is a caveat, not a penalty.** It never reorders anything. A provisional rating is
still the best estimate the games support, so it ranks where it ranks; demoting it would be a second
ranking rule, hidden inside a display flag.

**Unrated is not provisional.** An entrant with no ratable game has no number at all, and the pool
table says `unrated` (ADR-0027). Marking that provisional would imply a rating exists.

## Alternatives considered

**Tune the threshold to our data** — 150, say, so a few models cleared it. Rejected: the number
would then be a statement about wanting a tidy page rather than about confidence, and the first
person to ask "why 150?" would get no answer worth having.

**Show a games-played count instead of a flag.** The table already shows one, and it does not
answer the question: five games against one opponent settle less than five against five, which is
precisely the difference the deviation captures and a count cannot.

**Match Lichess's other constants too** — volatility 0.09, τ 0.75, and rating after every game
rather than in periods. Rejected, and the last one firmly: Glicko-2 is defined over batches, rating
game by game gives a different and less defensible answer, and our daily periods are closer to the
paper than Lichess is. The two constants adopted here are about the *prior* and about *presentation*,
neither of which changes the arithmetic.

## Consequences

**Every stored rating changes on deploy, and no migration is needed.** `compute_ratings` rebuilds
from the games on every request and `store_ratings` replaces the table wholesale, so the new prior
simply produces new numbers. Early ratings will be more spread out than before; the ordering is
driven by the same results.

**The `?` will be on every row for a while**, and that is the intended message rather than a defect
to fix. If it is still universal after the pool has run long enough for entrants to reach twenty-odd
games, the threshold is worth revisiting — but with evidence, not to tidy the page.

**`_decay` still has no ceiling on the deviation.** Glickman's system holds that a rating deviation
should not exceed the initial one — a contestant idle for years is no better known than a new one,
but no worse either — and ours grows without bound across idle periods. It is slow (about 200 points
in quadrature over a year at σ = 0.06) and nothing has hit it, so this records the gap rather than
closing it. Raising the prior to 500 makes the ceiling further away, not nearer.
