"""Bradley-Terry ratings with two imaginary draws each, and the order they are read in (ADR-0060).

**Why not Glicko-2 any more.** Glicko-2 rates a player whose strength drifts — rating periods, a
deviation that widens while idle, a volatility — and a model is a fixed set of weights that does
not. Over `pool-free`'s games its places moved by up to seventeen when the *same* results were dealt
in a different order, and a model that stopped playing had its deviation widened by every day the
others played. One fit over every game at once has neither problem: the answer depends on who beat
whom and on nothing else.

**The prior is two draws against an imaginary 1500 opponent**, inside the fit rather than bolted on.
Without one a 3/0/0 record has no finite rating; with an unstated one, Glicko-2's starting
deviation of 500, a three-game streak topped the table. Two was estimated from production's games
by `scripts/compare_ratings.py`, and it is a sentence a reader can check.

**Pure.** No I/O, no clock, no database. Keys are whatever the caller rates — the leaderboard rates
`(model, quantization)`, a test rates strings.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass

#: Where a contestant nobody has seen starts, and where the imaginary opponent sits.
CENTRE = 1500.0

#: Elo's scale: a 400-point gap is 10:1 odds, so the numbers read the way chess ratings do.
SCALE = 400.0 / math.log(10)

#: How many draws against the imaginary opponent every contestant starts with.
#:
#: Estimated, not chosen: the marginal likelihood and leave-one-out prediction on `pool-free` both
#: put the prior's width between 225 and 400 points, which is one to two of these. Two is the more
#: cautious end — it lets a short streak count for less — and a change here moves every rating on
#: the site, so it belongs in an ADR rather than a tuning pass (ADR-0060).
PRIOR_DRAWS = 2

#: Above this deviation a rating is **provisional** and says so — ADR-0028's mark, kept.
#:
#: 110 is Lichess's number, adopted verbatim when the engine was Glicko-2, and it still means what it
#: meant: a `± 110` band is two hundred points either way at 95%, which does not settle a placing.
PROVISIONAL_DEVIATION = 110.0

#: How many deviations below the rating a contestant has *proven*. Two is roughly 95% one-sided.
PROOF = 2.0


@dataclass(frozen=True, slots=True)
class Rating:
    """One contestant's standing: a best estimate, and the uncertainty around it."""

    rating: float = CENTRE
    #: The fit's own uncertainty, in rating points — the `±`. It shrinks with games and never grows,
    #: because nothing in the model involves time: a contestant that stops playing keeps it.
    #: A contestant with no games has only the imaginary draws: each pins it down by ¼ (the
    #: logistic's curvature at evens), so its variance is `4 / PRIOR_DRAWS` — ± 246 at two.
    rd: float = SCALE * math.sqrt(4.0 / PRIOR_DRAWS)

    @property
    def provisional(self) -> bool:
        """Still too unsure to read as a placing. Derived, so it cannot drift from the deviation."""
        return self.rd > PROVISIONAL_DEVIATION

    @property
    def proven(self) -> float:
        """The rating this contestant has shown it is at least — what a table is ordered by.

        `rating` alone left two 3/0/0 records above a 10/0/2 one by a few dozen points that mean
        nothing: the best estimates are level, and what a reader means by "ten wins is better" is
        that it has been *shown* more. More games at the same rate raise this; more wins at a worse
        rate do not; a contestant that stops playing keeps it and stops adding to it.
        """
        return self.rating - PROOF * self.rd


def standing_key(rating: Rating) -> tuple[float, float]:
    """The sort key, best first, for anything ordered by rating: proven strength, then rating.

    One function because the leaderboard and a pool's table must not disagree about what "better"
    means. The caller appends its own key as the last tie-break, so the order is total.
    """
    return (-rating.proven, -rating.rating)


def fit[K: Hashable](
    games: Sequence[tuple[K, K, float]], contestants: Iterable[K] = ()
) -> dict[K, Rating]:
    """Every contestant's rating from every game at once.

    `games` is `(white, black, white's score)`, the score 1, ½ or 0 — a draw is half a win, as it was
    under Glicko-2. `contestants` adds anyone who should be rated without having played; everyone
    who appears in a game is rated anyway.

    The same games give the same ratings in any order, which is the property the change was for.
    """
    keys = list(dict.fromkeys([*contestants, *(k for g in games for k in g[:2])]))
    if not keys:
        return {}
    index = {k: i for i, k in enumerate(keys)}
    pairs = [(index[w], index[b], s) for w, b, s in games]
    theta = _maximise(len(keys), pairs)
    precision = _precision(theta, pairs)
    variance = _inverse_diagonal(precision)
    return {
        k: Rating(rating=CENTRE + theta[i] * SCALE, rd=math.sqrt(variance[i]) * SCALE)
        for k, i in index.items()
    }


def _sigmoid(x: float) -> float:
    # Split so neither branch overflows: a 3/0/0 record against weak opponents sits far out.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _log_posterior(theta: list[float], pairs: list[tuple[int, int, float]]) -> float:
    total = 0.0
    for i, j, s in pairs:
        p = _sigmoid(theta[i] - theta[j])
        total += s * math.log(p) + (1.0 - s) * math.log(1.0 - p)
    # The imaginary draws: half a win and half a loss each against an opponent fixed at 0.
    for t in theta:
        p = _sigmoid(t)
        total += PRIOR_DRAWS * 0.5 * (math.log(p) + math.log(1.0 - p))
    return total


def _gradient(theta: list[float], pairs: list[tuple[int, int, float]]) -> list[float]:
    grad = [PRIOR_DRAWS * (0.5 - _sigmoid(t)) for t in theta]
    for i, j, s in pairs:
        r = s - _sigmoid(theta[i] - theta[j])
        grad[i] += r
        grad[j] -= r
    return grad


def _precision(theta: list[float], pairs: list[tuple[int, int, float]]) -> list[list[float]]:
    """The negative Hessian of the log posterior: how sharply each estimate is pinned down."""
    n = len(theta)
    out = [[0.0] * n for _ in range(n)]
    for k, t in enumerate(theta):
        p = _sigmoid(t)
        out[k][k] = PRIOR_DRAWS * p * (1.0 - p)
    for i, j, _ in pairs:
        p = _sigmoid(theta[i] - theta[j])
        w = p * (1.0 - p)
        out[i][i] += w
        out[j][j] += w
        out[i][j] -= w
        out[j][i] -= w
    return out


def _maximise(n: int, pairs: list[tuple[int, int, float]]) -> list[float]:
    """Newton's method, halving any step that does not improve the posterior.

    The posterior is strictly concave — the imaginary draws see to that even for a contestant with
    no games, or one whose games are all against a single opponent — so there is one answer and
    this finds it. The halving is for the far tails, where a full Newton step from 0 towards a
    lopsided record can overshoot.
    """
    theta = [0.0] * n
    current = _log_posterior(theta, pairs)
    for _ in range(100):
        step = _solve(_precision(theta, pairs), _gradient(theta, pairs))
        scale = 1.0
        while True:
            candidate = [t + scale * d for t, d in zip(theta, step, strict=True)]
            value = _log_posterior(candidate, pairs)
            if value >= current:
                break
            scale /= 2
            if scale < 1e-9:
                # No step improves on where we are: that is the maximum, to rounding.
                return theta
        theta, current = candidate, value
        if max(abs(scale * d) for d in step) < 1e-10:
            break
    return theta


def _cholesky(a: list[list[float]]) -> list[list[float]]:
    """`a = L·Lᵀ` for a symmetric positive-definite `a`, which every precision here is."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            low[i][j] = math.sqrt(s) if i == j else s / low[j][j]
    return low


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    low = _cholesky(a)
    return _back(low, _forward(low, b))


def _forward(low: list[list[float]], b: list[float]) -> list[float]:
    y: list[float] = []
    for i, row in enumerate(low):
        y.append((b[i] - sum(row[k] * y[k] for k in range(i))) / row[i])
    return y


def _back(low: list[list[float]], y: list[float]) -> list[float]:
    n = len(y)
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


def _inverse_diagonal(a: list[list[float]]) -> list[float]:
    """The diagonal of `a⁻¹` — each contestant's variance — without forming the whole inverse."""
    low = _cholesky(a)
    n = len(a)
    out = []
    for i in range(n):
        e = [1.0 if k == i else 0.0 for k in range(n)]
        out.append(_back(low, _forward(low, e))[i])
    return out
