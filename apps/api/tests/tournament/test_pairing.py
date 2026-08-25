"""Pairings: who plays whom, how often, and with which colour.

The properties here are the ones a standings table silently depends on. A round robin that pairs
someone twice in a round, or a Swiss that issues a rematch while an alternative existed, produces
a table that looks perfectly reasonable and means nothing.
"""

from __future__ import annotations

from collections import Counter

import pytest

from chessmark.tournament import Entrant, Format, Result, TournamentConfig, round_robin, schedule
from chessmark.tournament.pairing import swiss_round


def field(n: int) -> list[Entrant]:
    return [Entrant(key=f"m{i}", seed=i) for i in range(1, n + 1)]


def played_pairs(rounds: list[list]) -> Counter[frozenset[str]]:
    return Counter(game.pair for games in rounds for game in games)


# ====================================================================== round robin


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 8, 9, 12])
def test_everyone_meets_everyone_exactly_once(n: int) -> None:
    rounds = round_robin(field(n))
    counts = played_pairs(rounds)

    real = {pair: count for pair, count in counts.items() if len(pair) == 2}
    assert len(real) == n * (n - 1) // 2, "every pair must appear"
    assert set(real.values()) == {1}, "and exactly once"


@pytest.mark.parametrize("n", [4, 5, 8, 9])
def test_nobody_plays_twice_in_one_round(n: int) -> None:
    """The failure a naive schedule makes, and the one that invalidates a whole event."""
    for games in round_robin(field(n)):
        appearances = Counter(key for game in games for key in game.pair)
        assert appearances and max(appearances.values()) == 1


def test_an_odd_field_gives_each_entrant_exactly_one_bye() -> None:
    rounds = round_robin(field(5))

    byes = Counter(game.white for games in rounds for game in games if game.is_bye)

    assert len(rounds) == 5, "an odd field needs a round per entrant"
    assert byes == Counter({f"m{i}": 1 for i in range(1, 6)})


def test_an_even_field_has_no_byes() -> None:
    rounds = round_robin(field(8))

    assert not [game for games in rounds for game in games if game.is_bye]
    assert len(rounds) == 7
    assert all(len(games) == 4 for games in rounds)


def test_eight_models_played_twice_is_fifty_six_games() -> None:
    """The Phase 13 exit criterion, in one assertion."""
    rounds = round_robin(field(8), double=True)

    games = [game for games in rounds for game in games]

    assert len(games) == 56
    assert len(rounds) == 14


def test_doubling_reverses_every_colour() -> None:
    """Otherwise the second half measures the same colour advantage as the first."""
    single = round_robin(field(4))
    double = round_robin(field(4), double=True)

    first = [(g.white, g.black) for games in single for g in games]
    second = [(g.white, g.black) for games in double[len(single) :] for g in games]

    assert second == [(black, white) for white, black in first]


def colour_spread(rounds: list[list]) -> dict[str, int]:
    whites: Counter[str] = Counter()
    blacks: Counter[str] = Counter()
    for games in rounds:
        for game in games:
            whites[game.white] += 1
            if game.black:
                blacks[game.black] += 1
    return {key: whites[key] - blacks[key] for key in set(whites) | set(blacks)}


@pytest.mark.parametrize("n", [4, 6, 8, 10, 12, 16])
def test_colours_are_as_balanced_as_an_even_field_allows(n: int) -> None:
    """One is the floor, not a concession: an even field plays an odd number of rounds, so
    somebody must end up a colour ahead. Every obvious parity rule does far worse — slot parity
    gave one entrant White twice and Black eleven times in a field of twelve."""
    spread = colour_spread(round_robin(field(n)))

    assert max(abs(v) for v in spread.values()) <= 1, spread


@pytest.mark.parametrize("n", [5, 7, 9, 11])
def test_an_odd_field_stays_within_two(n: int) -> None:
    """Byes make an odd field harder to balance, and local search does not always find the
    optimum. Two is a mild bias over a whole event, and a double round robin removes it entirely."""
    spread = colour_spread(round_robin(field(n)))

    assert max(abs(v) for v in spread.values()) <= 2, spread


def test_a_double_round_robin_balances_colours_exactly() -> None:
    """By construction: the second half is the first with the sides swapped."""
    spread = colour_spread(round_robin(field(8), double=True))

    assert set(spread.values()) == {0}, spread


def test_a_field_too_small_to_pair_plays_nothing() -> None:
    assert round_robin(field(1)) == []
    assert round_robin([]) == []


# ====================================================================== swiss


def test_swiss_pairs_the_whole_field_each_round() -> None:
    games = swiss_round(field(8), [], 1)

    assert len(games) == 4
    assert {k for g in games for k in g.pair} == {f"m{i}" for i in range(1, 9)}


def test_swiss_pairs_leaders_together_in_the_second_round() -> None:
    """The property that makes Swiss work: winners meet winners, so a leader emerges."""
    entrants = field(4)
    first = [
        Result(white="m1", black="m2", white_score=1.0, round_number=1),
        Result(white="m3", black="m4", white_score=1.0, round_number=1),
    ]

    second = swiss_round(entrants, first, 2)

    assert len(second) == 2
    winners = {"m1", "m3"}
    assert any(g.pair == frozenset(winners) for g in second), "the two winners must meet"


def test_swiss_never_issues_a_rematch_while_an_alternative_exists() -> None:
    """The constraint that makes greedy pairing wrong. Four players, three rounds, all distinct."""
    entrants = field(4)
    results: list[Result] = []

    for round_number in range(1, 4):
        games = swiss_round(entrants, results, round_number)
        for game in games:
            assert game.black is not None
            results.append(
                Result(
                    white=game.white,
                    black=game.black,
                    white_score=1.0,
                    round_number=round_number,
                )
            )

    pairs = Counter(frozenset(r.players) for r in results)
    assert set(pairs.values()) == {1}, f"a rematch was issued: {pairs}"
    assert len(pairs) == 6, "four players over three rounds is every pairing exactly once"


def test_swiss_gives_the_bye_to_a_different_entrant_each_round() -> None:
    """A second bye to one player hands them two free points and distorts the table."""
    entrants = field(5)
    results: list[Result] = []
    byes: list[str] = []

    for round_number in range(1, 4):
        games = swiss_round(entrants, results, round_number)
        for game in games:
            if game.is_bye:
                byes.append(game.white)
                results.append(
                    Result(white=game.white, black=None, white_score=1.0, round_number=round_number)
                )
            else:
                results.append(
                    Result(
                        white=game.white,
                        black=game.black,
                        white_score=0.5,
                        round_number=round_number,
                    )
                )

    assert len(byes) == 3
    assert len(set(byes)) == 3, f"the bye repeated: {byes}"


def test_swiss_evens_out_colours() -> None:
    """White scores better, so a Swiss that never rebalances partly measures luck."""
    entrants = field(4)
    results = [
        Result(white="m1", black="m2", white_score=1.0, round_number=1),
        Result(white="m3", black="m4", white_score=1.0, round_number=1),
    ]

    second = swiss_round(entrants, results, 2)

    # m1 and m3 both had White; whoever they meet should not hand them White again.
    for game in second:
        if game.pair == frozenset({"m1", "m3"}):
            assert game.white in {"m1", "m3"}
        else:
            assert game.white in {"m2", "m4"}, "the players owed White should get it"


# ====================================================================== the schedule


def test_a_round_robin_is_scheduled_in_full_up_front() -> None:
    rounds = schedule(field(4), TournamentConfig(format=Format.ROUND_ROBIN))

    assert len(rounds) == 3


def test_a_swiss_is_scheduled_one_round_at_a_time() -> None:
    """Round two depends on round one, so it cannot be known yet — and that is what makes a
    tournament resumable rather than dependent on a scheduler's memory."""
    rounds = schedule(field(8), TournamentConfig(format=Format.SWISS, rounds=5))

    assert len(rounds) == 1
