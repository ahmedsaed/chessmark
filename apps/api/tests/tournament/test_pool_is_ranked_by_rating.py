"""A pool's table is ordered by a rating computed over that pool (ADR-0027).

Points rank a closed event because everybody plays the same schedule. A pool has no schedule: it
runs indefinitely, its field changes with the catalogue, and in the live `pool-free` entrants had
completed between **0 and 10** games. A sum then partly measures how many games a model was handed
— `dots-3-note-preview` won all five it played and stood *third*, behind a model on 6.0 from eight.
Sonneborn-Berger is another sum and does not help.

The first test is that shape exactly, because it is the one a reader notices.
"""

from __future__ import annotations

from chessmark.tournament import Entrant, Result, standings


def _field(*keys: str) -> list[Entrant]:
    return [Entrant(key=key, label=key, seed=index + 1) for index, key in enumerate(keys)]


def test_a_perfect_record_outranks_a_longer_one() -> None:
    """Five from five above six from eight. Under points it is the other way round, which is the
    finding that produced this module's change."""
    field = _field("perfect", "prolific")
    results = [
        Result(white="perfect", black="prolific", white_score=1.0, round_number=n + 1)
        for n in range(5)
    ]
    ratings = {"perfect": (1780.0, 90.0), "prolific": (1610.0, 70.0)}

    table = standings(field, results, ratings)

    assert [s.key for s in table] == ["perfect", "prolific"]
    assert table[0].place == 1
    assert table[0].rating == 1780.0
    assert table[0].rating_deviation == 90.0


def test_points_still_rank_a_closed_event() -> None:
    """No ratings passed, no change: a round robin gives everybody the same schedule, so a sum of
    points is what the event is for and a rating would be a second answer nobody asked for."""
    field = _field("a", "b")
    results = [Result(white="b", black="a", white_score=1.0, round_number=1)]

    table = standings(field, results)

    assert [s.key for s in table] == ["b", "a"]
    assert all(s.rating is None and s.rating_deviation is None for s in table)


def test_the_deviation_breaks_a_tie_on_rating() -> None:
    """Two equal ratings are not equally known, and the better-measured one is the stronger claim."""
    field = _field("vague", "settled")
    ratings = {"vague": (1600.0, 300.0), "settled": (1600.0, 45.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["settled", "vague"]


def test_an_unrated_entrant_sorts_last_not_mid_table() -> None:
    """**An unrated model is not an average model.** Defaulting it to 1500 would seat a model
    nobody has measured above every model measured below 1500 — precisely the claim the rating
    deviation exists to avoid making. `pool-free` had three such entrants, all of whom had never
    completed a game."""
    field = _field("strong", "weak", "unseen")
    ratings = {"strong": (1700.0, 80.0), "weak": (1300.0, 80.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["strong", "weak", "unseen"]
    assert table[-1].rating is None


def test_the_unrated_share_a_place_and_the_rated_never_do() -> None:
    """Two models with no games are not ranked against each other; two floats computed from
    different games are only equal by accident, and sharing a place on that would claim an
    inseparability the arithmetic never found."""
    field = _field("rated", "nothing", "also-nothing")
    ratings = {"rated": (1700.0, 80.0)}

    table = standings(field, [], ratings)

    places = {s.key: s.place for s in table}
    assert places["rated"] == 1
    assert places["nothing"] == places["also-nothing"] == 2


def test_the_score_columns_survive_the_reordering() -> None:
    """Points stop deciding the order; they do not stop being true. The table still has to show
    what a model actually did, or a reader cannot check the rating against anything."""
    field = _field("winner", "loser")
    results = [Result(white="winner", black="loser", white_score=1.0, round_number=1)]

    table = standings(field, results, {"winner": (1700.0, 80.0), "loser": (1300.0, 80.0)})

    assert table[0].score == 1.0
    assert table[0].wins == 1
    assert table[1].losses == 1
    assert table[1].score == 0.0
