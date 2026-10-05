"""The rating engine, tested pure (ADR-0060).

The properties are the reasons it replaced Glicko-2, each stated as something a reader of the
leaderboard would notice if it broke: the same games give the same ratings in any order; a short
perfect record is finite and cautious; more of the same record proves more; and a model that stops
playing keeps what it had.
"""

from __future__ import annotations

import itertools
import math

from chessmark.bench import CENTRE, PRIOR_DRAWS, Rating, fit, standing_key
from chessmark.bench.bradley_terry import SCALE


def _wins(winner: str, loser: str, n: int) -> list[tuple[str, str, float]]:
    return [(winner, loser, 1.0)] * n


def test_nobody_seen_is_1500_and_as_unsure_as_two_draws_make_it() -> None:
    """Two draws against an even opponent pin a strength down by ¼ each on the natural scale, so
    the variance is 4 / PRIOR_DRAWS. That is the whole prior, and it is what a reader is told."""
    rated = fit([], ["unseen"])["unseen"]

    assert rated.rating == CENTRE
    assert math.isclose(rated.rd, SCALE * math.sqrt(4.0 / PRIOR_DRAWS))
    default = Rating()
    assert math.isclose(rated.rd, default.rd), "the default and the fit agree about nobody seen"


def test_the_fit_is_the_maximum_a_hand_solved_equation_gives() -> None:
    """An independent check of the arithmetic, not of its plumbing. A beats B three times; by
    symmetry the strengths are ±t, and the posterior is flat where

        3 * (1 - sigmoid(2t)) + PRIOR_DRAWS * (0.5 - sigmoid(t)) = 0

    which bisection solves without touching the module's Newton step or its linear algebra."""

    def sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    def slope(t: float) -> float:
        return 3 * (1 - sigmoid(2 * t)) + PRIOR_DRAWS * (0.5 - sigmoid(t))

    low, high = 0.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        low, high = (mid, high) if slope(mid) > 0 else (low, mid)

    ratings = fit(_wins("a", "b", 3))

    assert math.isclose(ratings["a"].rating, CENTRE + low * SCALE, abs_tol=1e-6)
    assert math.isclose(ratings["b"].rating, CENTRE - low * SCALE, abs_tol=1e-6)


def test_the_same_games_in_any_order_give_the_same_ratings() -> None:
    """The property Glicko-2 lacked. Over `pool-free` it placed one model anywhere from 1st to 13th
    depending on the order identical results were dealt in; a fixed model has one strength."""
    games = [
        ("a", "b", 1.0),
        ("b", "c", 0.5),
        ("c", "a", 0.0),
        ("a", "c", 1.0),
        ("d", "b", 0.0),
    ]
    reference = fit(games)

    # Equal to the last few bits of a float: summing the same terms in another order rounds
    # differently, which is arithmetic and not a dependence on order.
    for order in itertools.permutations(games):
        shuffled = fit(list(order))
        for key, rating in reference.items():
            assert math.isclose(shuffled[key].rating, rating.rating, abs_tol=1e-9)
            assert math.isclose(shuffled[key].rd, rating.rd, abs_tol=1e-9)


def test_a_short_perfect_record_is_finite_and_cautious() -> None:
    """Without a prior 3/0/0 is infinitely strong; with Glicko-2's unstated one it topped the table.
    Twenty straight wins must not overflow either — the sigmoid is split for exactly this tail."""
    three = fit(_wins("streak", "victim", 3))["streak"]
    twenty = fit(_wins("streak", "victim", 20))["streak"]

    assert CENTRE < three.rating < CENTRE + 400, "three wins over one opponent is not 10:1 odds"
    assert math.isfinite(twenty.rating) and twenty.rating > three.rating


def test_more_of_the_same_record_proves_more() -> None:
    """10/0/0 above 6/0/0 above 3/0/0, against the same opponent — each extra win narrows the ± and
    raises what has been proven, which is what "more won games means stronger" should mean."""
    proven = [fit(_wins("model", "anchor", n))["model"].proven for n in (3, 6, 10)]

    assert proven == sorted(proven)
    assert len(set(proven)) == 3


def test_a_longer_record_is_ranked_above_a_streak_with_the_same_estimate() -> None:
    """The case that started it. A record over twelve games and a streak over three can have level
    best estimates; ordering by proven strength puts the record first."""
    streak = Rating(rating=1781.0, rd=209.0)
    record = Rating(rating=1743.0, rd=134.0)

    assert sorted([streak, record], key=standing_key) == [record, streak]


def test_a_game_never_widens_anyone_s_deviation() -> None:
    """Nothing in the model involves time, so the ± only shrinks with evidence. Under Glicko-2 a
    model that stopped playing was made *less* certain by every day the others played."""
    before = fit([("a", "b", 1.0)])
    after = fit([("a", "b", 1.0), ("b", "c", 0.5), ("c", "d", 1.0)])

    for key in ("a", "b"):
        assert after[key].rd <= before[key].rd


def test_a_retired_model_is_re_evaluated_through_the_models_it_beat() -> None:
    """A model that stops playing keeps its record, and its rating still moves when the opponents
    it beat are re-measured. Beating a model later shown to be strong was worth more."""
    early = fit(_wins("retired", "opponent", 3))["retired"]
    later = fit([*_wins("retired", "opponent", 3), *_wins("opponent", "field", 6)])["retired"]

    assert later.rating > early.rating


def test_a_draw_is_half_a_win() -> None:
    """As under Glicko-2, so the change is in the model and not in what a result means."""
    drawn = fit([("a", "b", 0.5)] * 4)

    assert math.isclose(drawn["a"].rating, CENTRE, abs_tol=1e-9)
    assert math.isclose(drawn["b"].rating, CENTRE, abs_tol=1e-9)
