"""Glicko-2, as specified by Mark Glickman (BENCH-01).

Chosen over Elo because a benchmark's central problem is **confidence, not just order**. A model
that has played three games and one that has played three hundred should not be presented as
equally well known, and Elo has no way to say so. Glicko-2 carries a rating deviation — the
leaderboard can print 1650 ± 40 next to 1650 ± 300 and let a reader see which one means something.

Implemented from the paper rather than taken from a library, because the exit criterion is
reproducing Glickman's own worked example to three decimal places, and a dependency that happened
to agree would demonstrate nothing about our arithmetic.

**Pure.** No I/O, no clock, no database. Ratings are computed from numbers handed in, which is what
makes the determinism criterion checkable at all.

Reference: Glickman, M. E. (2013), "Example of the Glicko-2 system", glicko.net/glicko/glicko2.pdf
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: A player nobody has seen. 1500 is conventional; the deviation is what says "we know nothing".
DEFAULT_RATING = 1500.0

#: How unsure we are of a contestant nobody has seen.
#:
#: **500, following Lichess, rather than Glickman's 350.** The initial deviation is a statement
#: about how fast the first few games are allowed to move a rating, and 350 is calibrated for a
#: rating pool where a new player is a rare event among many settled ones. Ours is the opposite:
#: the matchmaker deliberately pairs whoever is *least* known (`tournament/matchmaking.py`), so the
#: population is mostly new and the games we spend are mostly spent on models that have barely
#: played. A wider prior lets those first games count for what they are worth.
#:
#: It is a prior, not a licence: the same evidence still produces the same ordering, and the
#: deviation is published beside the rating so a fast-moving early number cannot be mistaken for a
#: settled one.
DEFAULT_RD = 500.0

DEFAULT_VOLATILITY = 0.06

#: Above this deviation a rating is **provisional** and says so.
#:
#: 110, Lichess's threshold, adopted verbatim rather than tuned — a number chosen to make our own
#: table look settled would be worth nothing. `± 208` is honest and most readers do not know what
#: to do with it; "provisional" is the same fact in a word.
#:
#: **Every contestant is provisional today**, at 150 to 265 over two to nine games each, and that
#: is the correct thing for the page to say: nine games do not settle a rating. The flag starts
#: discriminating at roughly fifteen to twenty games, which is where a pool gets to on its own.
PROVISIONAL_RD = 110.0

#: Glicko-2 works on an internal scale where a rating point is worth 1/173.7178.
SCALE = 173.7178

#: How much a rating is expected to fluctuate. Glickman suggests 0.3 to 1.2; smaller values make
#: ratings steadier and slower to react to a genuine change in strength. 0.5 is his worked example.
DEFAULT_TAU = 0.5

#: Convergence tolerance for the volatility solver, from the paper.
EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class Rating:
    """One contestant's standing. `rd` is the honest half — it is what a leaderboard must show."""

    # `provisional` is below, and is `rd` said in a word for readers who do not think in deviations.

    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOLATILITY

    @property
    def provisional(self) -> bool:
        """Whether this rating is still too unsure to be read as a placing.

        Derived rather than stored, so it cannot drift from the deviation it describes.
        """
        return self.rd > PROVISIONAL_RD

    @property
    def mu(self) -> float:
        return (self.rating - DEFAULT_RATING) / SCALE

    @property
    def phi(self) -> float:
        return self.rd / SCALE

    @classmethod
    def from_internal(cls, mu: float, phi: float, volatility: float) -> Rating:
        return cls(
            rating=mu * SCALE + DEFAULT_RATING,
            rd=phi * SCALE,
            volatility=volatility,
        )


@dataclass(frozen=True, slots=True)
class Outcome:
    """One game against one opponent.

    `score` is 1 for a win, 0.5 for a draw, 0 for a loss. Nothing else is meaningful — a game that
    ended for a reason neither player caused should not reach here at all, and deciding that is the
    caller's job (see `bench/ratable.py`).
    """

    opponent: Rating
    score: float


def _g(phi: float) -> float:
    """How much weight an opponent's result carries, discounted by how unsure we are of them."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, opponent_mu: float, opponent_phi: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(opponent_phi) * (mu - opponent_mu)))


class Glicko2:
    """A rating system at one value of τ."""

    def __init__(self, tau: float = DEFAULT_TAU) -> None:
        self.tau = tau

    def rate(self, player: Rating, outcomes: list[Outcome]) -> Rating:
        """The player's rating after one rating period.

        A period, not a game: Glicko-2 is defined over batches, and rating each game separately
        gives a different — and less defensible — answer than the system specifies.
        """
        if not outcomes:
            return self._decay(player)

        mu, phi = player.mu, player.phi

        variance = 0.0
        delta_sum = 0.0
        for outcome in outcomes:
            g = _g(outcome.opponent.phi)
            expected = _expected(mu, outcome.opponent.mu, outcome.opponent.phi)
            variance += g * g * expected * (1.0 - expected)
            delta_sum += g * (outcome.score - expected)

        v = 1.0 / variance
        delta = v * delta_sum

        volatility = self._new_volatility(phi, v, delta, player.volatility)

        # The rating deviation grows by the volatility before the games shrink it again. This is
        # what makes a long absence widen the uncertainty rather than freeze it.
        phi_star = math.sqrt(phi * phi + volatility * volatility)
        new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
        new_mu = mu + new_phi * new_phi * delta_sum

        return Rating.from_internal(new_mu, new_phi, volatility)

    def _decay(self, player: Rating) -> Rating:
        """A period with no games. The rating holds; the confidence in it does not.

        Not a formality — a model that played once in March and is still shown at ± 60 in December
        is a lie the leaderboard is telling.
        """
        phi_star = math.sqrt(player.phi**2 + player.volatility**2)
        return Rating.from_internal(player.mu, phi_star, player.volatility)

    def _new_volatility(self, phi: float, v: float, delta: float, volatility: float) -> float:
        """Solve for the new volatility by the Illinois variant of regula falsi.

        Step-by-step from the paper. It is not a formula but an iteration, and the bracketing is
        the fiddly part: the initial upper bound depends on whether the observed result was more
        surprising than the current uncertainty can explain.
        """
        a = math.log(volatility * volatility)
        phi_sq = phi * phi
        delta_sq = delta * delta
        tau_sq = self.tau * self.tau

        def f(x: float) -> float:
            exp_x = math.exp(x)
            denominator = phi_sq + v + exp_x
            return (
                exp_x * (delta_sq - phi_sq - v - exp_x) / (2.0 * denominator * denominator)
                - (x - a) / tau_sq
            )

        big_a = a
        if delta_sq > phi_sq + v:
            big_b = math.log(delta_sq - phi_sq - v)
        else:
            # Walk down in steps of τ until the function changes sign — the paper's procedure for
            # the case where the result is *less* surprising than the current spread.
            k = 1
            while f(a - k * self.tau) < 0:
                k += 1
            big_b = a - k * self.tau

        f_a, f_b = f(big_a), f(big_b)

        while abs(big_b - big_a) > EPSILON:
            c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
            f_c = f(c)

            if f_c * f_b <= 0:
                big_a, f_a = big_b, f_b
            else:
                # The Illinois halving. Plain regula falsi stalls here, and a stalled solver is a
                # rating that silently never converges.
                f_a /= 2.0

            big_b, f_b = c, f_c

        return math.exp(big_a / 2.0)
