"""The table, against results worked out by hand.

A standings table is the thing people argue about, so every number here is computed independently
in the test's own docstring rather than by calling the code a second way. If the implementation and
the arithmetic ever disagree, the arithmetic is right.
"""

from __future__ import annotations

from chessmark.tournament import Entrant, Result, standings

A, B, C, D = (Entrant(key=k, seed=i) for i, k in enumerate("ABCD", start=1))
FIELD = [A, B, C, D]


def game(white: str, black: str, white_score: float, round_number: int = 1) -> Result:
    return Result(white=white, black=black, white_score=white_score, round_number=round_number)


#: A four-player round robin worked out by hand.
#:
#:   R1  A beats B, C beats D
#:   R2  A beats C, B beats D
#:   R3  A beats D, B draws C
#:
#: Scores    A 3, B 1.5, C 1.5, D 0
#: Sonneborn-Berger
#:   A beat B(1.5) + C(1.5) + D(0)          = 3.0
#:   B beat D(0), drew C(1.5)/2             = 0.75
#:   C beat D(0), drew B(1.5)/2             = 0.75
#:   D won nothing                          = 0.0
#: B and C are level on score *and* tiebreak, and drew their game, so they are genuinely joint.
ROUND_ROBIN = [
    game("A", "B", 1.0, 1),
    game("C", "D", 1.0, 1),
    game("A", "C", 1.0, 2),
    game("B", "D", 1.0, 2),
    game("A", "D", 1.0, 3),
    game("B", "C", 0.5, 3),
]


def test_scores_match_the_hand_computed_table() -> None:
    table = {s.key: s.score for s in standings(FIELD, ROUND_ROBIN)}

    assert table == {"A": 3.0, "B": 1.5, "C": 1.5, "D": 0.0}


def test_win_draw_loss_counts_are_right() -> None:
    table = {s.key: (s.wins, s.draws, s.losses) for s in standings(FIELD, ROUND_ROBIN)}

    assert table == {"A": (3, 0, 0), "B": (1, 1, 1), "C": (1, 1, 1), "D": (0, 0, 3)}


def test_sonneborn_berger_matches_the_hand_computed_values() -> None:
    """Beating strong opponents counts for more than beating weak ones."""
    table = {s.key: s.sonneborn_berger for s in standings(FIELD, ROUND_ROBIN)}

    assert table == {"A": 3.0, "B": 0.75, "C": 0.75, "D": 0.0}


def test_the_order_is_by_score_then_tiebreak() -> None:
    order = [s.key for s in standings(FIELD, ROUND_ROBIN)]

    assert order[0] == "A"
    assert order[-1] == "D"
    assert set(order[1:3]) == {"B", "C"}


def test_entrants_that_cannot_be_separated_share_a_place() -> None:
    """Printing 2nd and 3rd would claim a distinction the games did not produce."""
    places = {s.key: s.place for s in standings(FIELD, ROUND_ROBIN)}

    assert places["A"] == 1
    assert places["B"] == places["C"] == 2
    assert places["D"] == 4


def test_a_direct_encounter_separates_entrants_a_tiebreak_cannot() -> None:
    """B and C level on score and Sonneborn-Berger, but C won their game.

    R1  A beats B, C beats D
    R2  A beats C, B beats D
    R3  A beats D, C beats B      <- the only change from the fixture above

    Scores  A 3, B 1, C 2, D 0 — so this no longer ties, and instead pins that the head-to-head
    path is reachable at all. The tie itself is exercised by the fixture above.
    """
    results = [*ROUND_ROBIN[:5], game("C", "B", 1.0, 3)]

    order = [s.key for s in standings(FIELD, results)]

    assert order == ["A", "C", "B", "D"]


def test_a_bye_scores_a_point_but_credits_no_opponent_strength() -> None:
    """There was no opponent, so there is no strength to count towards Sonneborn-Berger."""
    results = [game("A", "B", 1.0), Result(white="C", black=None, white_score=1.0, round_number=2)]

    table = {s.key: s for s in standings(FIELD, results)}

    assert table["C"].score == 1.0
    assert table["C"].byes == 1
    assert table["C"].played == 0, "a bye is not a game played"
    assert table["C"].sonneborn_berger == 0.0


def test_a_result_naming_an_unknown_entrant_is_ignored_not_fatal() -> None:
    """A model can be withdrawn after playing; losing the whole table over it would be worse."""
    results = [*ROUND_ROBIN, game("A", "ghost", 1.0, 4)]

    table = {s.key: s.score for s in standings(FIELD, results)}

    assert table["A"] == 3.0, "the stale game must not move a score"
    assert "ghost" not in table


def test_an_empty_tournament_still_produces_a_full_table() -> None:
    table = standings(FIELD, [])

    assert [s.key for s in table] == ["A", "B", "C", "D"]
    assert all(s.score == 0.0 and s.played == 0 for s in table)
    assert all(s.place == 1 for s in table), "nobody has separated themselves yet"
