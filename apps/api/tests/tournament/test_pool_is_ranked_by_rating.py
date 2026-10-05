"""A pool's table is ordered by a rating computed over that pool (ADR-0027).

Points rank a closed event because everybody plays the same schedule. A pool has no schedule: it
runs indefinitely, its field changes with the catalogue, and in the live `pool-free` entrants had
completed between **0 and 10** games. A sum then partly measures how many games a model was handed
— `dots-3-note-preview` won all five it played and stood *third*, behind a model on 6.0 from eight.
Sonneborn-Berger is another sum and does not help.

The first test is that shape exactly, because it is the one a reader notices.

The order within a rating is **proven strength**, the rating less two deviations (ADR-0060), so
ratings are built here through `bench.Rating` rather than written as tuples by hand: the rule is
the rating system's, and a test that restated it could agree with a wrong copy.
"""

from __future__ import annotations

from chessmark.bench import Rating
from chessmark.tournament import Entrant, Result, standings


def _field(*keys: str) -> list[Entrant]:
    return [Entrant(key=key, label=key, seed=index + 1) for index, key in enumerate(keys)]


def _rated(rating: float, rd: float) -> tuple[float, float, bool, float]:
    """What `ratings_by_key` hands the table for one entrant."""
    r = Rating(rating=rating, rd=rd)
    return (r.rating, r.rd, r.provisional, r.proven)


def test_a_perfect_record_outranks_a_longer_one() -> None:
    """Five from five above six from eight. Under points it is the other way round, which is the
    finding that produced this module's change."""
    field = _field("perfect", "prolific")
    results = [
        Result(white="perfect", black="prolific", white_score=1.0, round_number=n + 1)
        for n in range(5)
    ]
    ratings = {"perfect": _rated(1780.0, 90.0), "prolific": _rated(1610.0, 70.0)}

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
    """Two equal ratings are not equally known, and the better-measured one has proven more."""
    field = _field("vague", "settled")
    ratings = {"vague": _rated(1600.0, 300.0), "settled": _rated(1600.0, 45.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["settled", "vague"]


def test_a_longer_record_outranks_a_streak_with_the_same_estimate() -> None:
    """The question that produced ADR-0060, with `pool-free`'s own numbers. Nex-N2.5-Pro at 3/0/0
    and Qwen3.8-27B at 10/0/2 have best estimates 38 points apart — level, to any reader who knows
    what ± 209 means — and ordering on the estimate put the streak first. Proven strength puts
    the record first, which is what "ten wins is better" means."""
    field = _field("streak", "record")
    ratings = {"streak": _rated(1781.0, 209.0), "record": _rated(1743.0, 134.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["record", "streak"]
    assert table[0].rating is not None and table[1].rating is not None
    assert table[0].rating < table[1].rating, "the order is not the rating's"


def test_an_unrated_entrant_sorts_last_not_mid_table() -> None:
    """**An unrated model is not an average model.** Defaulting it to 1500 would seat a model
    nobody has measured above every model measured below 1500 — precisely the claim the rating
    deviation exists to avoid making. `pool-free` had three such entrants, all of whom had never
    completed a game."""
    field = _field("strong", "weak", "unseen")
    ratings = {"strong": _rated(1700.0, 80.0), "weak": _rated(1300.0, 80.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["strong", "weak", "unseen"]
    assert table[-1].rating is None


def test_the_unrated_share_a_place_and_the_rated_never_do() -> None:
    """Two models with no games are not ranked against each other; two floats computed from
    different games are only equal by accident, and sharing a place on that would claim an
    inseparability the arithmetic never found."""
    field = _field("rated", "nothing", "also-nothing")
    ratings = {"rated": _rated(1700.0, 80.0)}

    table = standings(field, [], ratings)

    places = {s.key: s.place for s in table}
    assert places["rated"] == 1
    assert places["nothing"] == places["also-nothing"] == 2


def test_the_score_columns_survive_the_reordering() -> None:
    """Points stop deciding the order; they do not stop being true. The table still has to show
    what a model actually did, or a reader cannot check the rating against anything."""
    field = _field("winner", "loser")
    results = [Result(white="winner", black="loser", white_score=1.0, round_number=1)]

    table = standings(
        field, results, {"winner": _rated(1700.0, 80.0), "loser": _rated(1300.0, 80.0)}
    )

    assert table[0].score == 1.0
    assert table[0].wins == 1
    assert table[1].losses == 1
    assert table[1].score == 0.0


def test_the_provisional_flag_is_carried_but_never_reorders() -> None:
    """The deviation moves a row — that is what proven strength is — but the `?` derived from it
    is a caveat for the reader, not a second rule. A provisional entrant that has proven more still
    ranks above a settled one that has proven less."""
    field = _field("unsure", "sure")
    ratings = {"unsure": _rated(1950.0, 115.0), "sure": _rated(1600.0, 50.0)}

    table = standings(field, [], ratings)

    assert [s.key for s in table] == ["unsure", "sure"]
    assert table[0].rating_provisional
    assert not table[1].rating_provisional


def test_an_unrated_entrant_is_not_provisional() -> None:
    """Two different statements. "Provisional" says we measured it and are unsure; "unrated" says
    we have not measured it. Flagging the second as the first would imply a number exists."""
    table = standings(_field("rated", "unseen"), [], {"rated": _rated(1700.0, 80.0)})

    unseen = next(s for s in table if s.key == "unseen")
    assert unseen.rating is None
    assert not unseen.rating_provisional
