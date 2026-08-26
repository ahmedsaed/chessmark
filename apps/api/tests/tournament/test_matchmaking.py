"""Choosing the next game in a pool.

The policy exists to make ratings converge, so the tests are about information rather than
fairness: does a newly-listed model get played, and are the games it gets informative ones?
"""

from __future__ import annotations

from chessmark.tournament import Entrant, Form, Result, matchmake

FIELD = [Entrant(key=k, seed=i) for i, k in enumerate("ABCD", start=1)]


def form(**by_key: tuple[float, float]) -> dict[str, Form]:
    """`{"A": (rating, deviation)}` — everything unnamed keeps the default 1500 ± 350."""
    return {
        key: Form(key=key, rating=rating, deviation=deviation)
        for key, (rating, deviation) in by_key.items()
    }


SETTLED = form(A=(1500, 40), B=(1520, 40), C=(1800, 40), D=(1200, 40))


def test_the_least_known_entrant_is_played_first() -> None:
    """A model nobody has measured is where the next game is worth the most — which is exactly
    what makes a newly listed model settle rather than sit unrated."""
    newcomer = {**SETTLED, "D": Form(key="D", rating=1500, deviation=350)}

    games = matchmake(FIELD, [], newcomer)

    assert len(games) == 1
    assert "D" in games[0].pair


def test_the_opponent_is_the_closest_rated_one() -> None:
    """A foregone conclusion moves neither rating. Near-equals convert a game into information."""
    ratings = form(A=(1500, 350), B=(1520, 40), C=(1800, 40), D=(1200, 40))

    games = matchmake(FIELD, [], ratings)

    assert games[0].pair == frozenset({"A", "B"}), "1520 is nearest to 1500"


def test_an_unmet_opponent_beats_a_nearer_one_already_played() -> None:
    """Variety is worth more than a few rating points: a rematch tells us less than a new pairing."""
    ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
    already = [Result(white="A", black="B", white_score=1.0, round_number=1)]

    games = matchmake(FIELD, already, ratings)

    assert games[0].pair == frozenset({"A", "C"}), "B is nearer but has been played"


def test_nobody_is_paired_twice_in_one_batch() -> None:
    """Those games run concurrently; a model cannot play itself in two places at once."""
    games = matchmake(FIELD, [], SETTLED, count=2)

    assert len(games) == 2
    played = [key for game in games for key in game.pair]
    assert len(set(played)) == 4


def test_a_batch_does_not_repeat_a_pairing_it_just_made() -> None:
    """The batch has to reflect its own choices, or it would pick the same best pair twice."""
    big = [Entrant(key=f"m{i}", seed=i) for i in range(1, 7)]

    games = matchmake(big, [], {}, count=3)

    pairs = {game.pair for game in games}
    assert len(pairs) == 3


def test_it_stops_when_the_field_runs_out() -> None:
    games = matchmake(FIELD, [], SETTLED, count=10)

    assert len(games) == 2, "four entrants make at most two concurrent games"


def test_a_field_too_small_plays_nothing() -> None:
    assert matchmake([Entrant(key="A")], [], {}) == []
    assert matchmake([], [], {}) == []


def test_colours_even_out_over_time() -> None:
    """A pool runs for months. A model that drew White two thirds of the time would carry a
    rating partly measuring that."""
    ratings = form(A=(1500, 350), B=(1500, 350))
    pair = [Entrant(key="A", seed=1), Entrant(key="B", seed=2)]
    results: list[Result] = []

    whites: list[str] = []
    for round_number in range(1, 7):
        game = matchmake(pair, results, ratings, round_number=round_number)[0]
        whites.append(game.white)
        assert game.black is not None
        results.append(
            Result(white=game.white, black=game.black, white_score=0.5, round_number=round_number)
        )

    assert whites.count("A") == whites.count("B") == 3


def test_an_entrant_with_no_recorded_form_is_treated_as_unknown() -> None:
    """A model that has just joined has no rating yet — and unknown is exactly the state that
    should be prioritised, not skipped."""
    games = matchmake(FIELD, [], form(A=(1500, 40), B=(1500, 40), C=(1500, 40)))

    assert "D" in games[0].pair, "the one with no form is the least known"
