"""Choosing the next game in a pool.

Two policies, and the tests are split accordingly (ADR-0041).

`Policy.INFORMATION` was the only one there was, and it asks whether ratings converge: does a
newly-listed model get played, and are the games it gets informative ones? Those tests now name
the policy they were written for, because the default changed out from under them — which is the
point of naming it.

`Policy.BALANCE` is the default, and asks whether the field is covered evenly. Its tests are about
fairness, so they assert on counts and coverage rather than on a single choice: the property is
what the whole schedule looks like, not which pair comes first.
"""

from __future__ import annotations

import collections

import pytest

from chessmark.tournament import Entrant, Form, Pairing, Policy, Result, matchmake

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

    games = matchmake(FIELD, [], newcomer, policy=Policy.INFORMATION)

    assert len(games) == 1
    assert "D" in games[0].pair


def test_the_opponent_is_the_closest_rated_one() -> None:
    """A foregone conclusion moves neither rating. Near-equals convert a game into information."""
    ratings = form(A=(1500, 350), B=(1520, 40), C=(1800, 40), D=(1200, 40))

    games = matchmake(FIELD, [], ratings, policy=Policy.INFORMATION)

    assert games[0].pair == frozenset({"A", "B"}), "1520 is nearest to 1500"


def test_an_unmet_opponent_beats_a_nearer_one_already_played() -> None:
    """Variety is worth more than a few rating points: a rematch tells us less than a new pairing."""
    ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
    already = [Result(white="A", black="B", white_score=1.0, round_number=1)]

    games = matchmake(FIELD, already, ratings, policy=Policy.INFORMATION)

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
    games = matchmake(
        FIELD, [], form(A=(1500, 40), B=(1500, 40), C=(1500, 40)), policy=Policy.INFORMATION
    )

    assert "D" in games[0].pair, "the one with no form is the least known"


# ====================================================================== who cannot play right now


class TestUnavailable:
    """Skipping entrants whose endpoint is resting (OPS-13).

    This is a correction to the policy above, not a refinement of it. "Least known first" cannot
    escape a model that keeps failing: an abandoned game is excluded from ratings, so its deviation
    never moves, so it is *permanently* the least-known entrant and is chosen again. In production
    that paired one dark model fourteen consecutive times, each pairing dying at ply 0.
    """

    def test_an_unavailable_entrant_is_not_paired(self) -> None:
        """Even when it is the one the policy most wants to play — which it always is, because a
        model whose games keep failing is the one nothing is known about."""
        unknown = {**SETTLED, "D": Form(key="D", rating=1500, deviation=350)}

        games = matchmake(FIELD, [], unknown, unavailable={"D"})

        assert games
        assert all("D" not in (g.white, g.black) for g in games)

    def test_the_slot_goes_to_somebody_who_can_play(self) -> None:
        """The point of skipping rather than holding. A pool with a concurrency of one spent ninety
        minutes rediscovering that a provider was rate-limited; the fix is not to wait more
        politely, it is to play a different game."""
        unknown = {**SETTLED, "D": Form(key="D", rating=1500, deviation=350)}

        games = matchmake(FIELD, [], unknown, unavailable={"D"}, count=1)

        assert len(games) == 1

    def test_a_resting_entrant_is_still_a_rating_to_pair_against(self) -> None:
        """Skipped, not withdrawn. Its rating is real and is still what an opponent is chosen for
        proximity to; only its own turn to play is deferred."""
        games = matchmake(FIELD, [], SETTLED, unavailable={"A"}, count=1)

        assert games
        pair = {games[0].white, games[0].black}
        assert "A" not in pair
        assert pair == {"B", "C"} or pair == {"B", "D"} or pair == {"C", "D"}

    def test_too_few_left_pairs_nothing_rather_than_pairing_anyway(self) -> None:
        """The pool holds for a tick, which is correct: there is no game worth starting. Pairing
        regardless would put a game against a model that cannot answer."""
        assert matchmake(FIELD, [], SETTLED, unavailable={"A", "B", "C"}) == []

    def test_all_resting_is_not_an_error(self) -> None:
        assert matchmake(FIELD, [], SETTLED, unavailable={"A", "B", "C", "D"}) == []

    def test_nothing_unavailable_behaves_exactly_as_before(self) -> None:
        """The default has to be inert, or every closed format pays for a pool's problem."""
        assert matchmake(FIELD, [], SETTLED, count=2) == matchmake(
            FIELD, [], SETTLED, count=2, unavailable=set()
        )


# ================================================================ a pairing that produced nothing


class TestAttempts:
    """A rematch is counted from what was scheduled, not from what survived (OPS-21).

    The production failure: `gemma-4-26b` and `gemma-4-31b`, both unrated and both on the same
    rate-limited Google pool, were paired seven times over five days and never reached ply 1. Every
    game paused until the 24-hour patience ran out and was abandoned — which records no result, so
    `results` never saw the fixture, so the rematch penalty never fired, so the pool chose it again
    within the hour. Two entrants nothing was known about, and the one pairing guaranteed to keep
    it that way.
    """

    def test_an_abandoned_pairing_still_counts_as_a_rematch(self) -> None:
        """The bug, in one assertion. B is nearest to A and has no *result* against it — but they
        have already been put on the board, and doing it again is how the loop ran for five days."""
        ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
        dead = [Pairing(white="A", black="B", round_number=1)]

        games = matchmake(FIELD, [], ratings, attempts=dead, policy=Policy.INFORMATION)

        assert games[0].pair == frozenset({"A", "C"}), (
            "B is nearer, but that pairing has been tried and produced nothing"
        )

    def test_repeated_dead_attempts_are_not_forgotten_one_by_one(self) -> None:
        """A count, not a flag. Seven attempts must not read as one."""
        ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
        dead = [Pairing(white="A", black="B", round_number=n) for n in range(1, 8)]

        games = matchmake(FIELD, [], ratings, attempts=dead, policy=Policy.INFORMATION)

        assert "B" not in games[0].pair

    def test_a_result_and_an_attempt_are_both_meetings(self) -> None:
        """The two halves are complementary — settled games arrive as `results`, unsettled ones as
        `attempts` — so an entrant that has met everybody by either route is out of fresh
        opponents and falls back to rating proximity."""
        ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
        played = [Result(white="A", black="C", white_score=1.0, round_number=1)]
        dead = [Pairing(white="A", black="B", round_number=2)]

        games = matchmake(FIELD, played, ratings, attempts=dead, count=1, policy=Policy.INFORMATION)

        assert games[0].pair == frozenset({"A", "D"}), "the only opponent A has not been given"

    def test_a_bye_is_not_a_meeting(self) -> None:
        """There was no opponent, so there is nobody to have met."""
        ratings = form(A=(1500, 350), B=(1505, 40), C=(1600, 40), D=(1200, 40))
        dead = [Pairing(white="A", black=None, round_number=1)]

        games = matchmake(FIELD, [], ratings, attempts=dead, policy=Policy.INFORMATION)

        assert games[0].pair == frozenset({"A", "B"}), "the nearest rating, unaffected by the bye"

    def test_nothing_attempted_behaves_exactly_as_before(self) -> None:
        """The default has to be inert, or every closed format pays for a pool's problem."""
        assert matchmake(FIELD, [], SETTLED, count=2) == matchmake(
            FIELD, [], SETTLED, count=2, attempts=[]
        )


def test_each_pooled_game_gets_its_own_round_number() -> None:
    """A pool round is a batch of one (ADR-0031).

    A batch used to carry one `round_number` for every game in it, which is right for a Swiss round
    — those games *are* one round, paired together off one set of standings. A pool has no such
    thing: each game is matched independently, against ratings the previous game in the same batch
    has already moved, and the batch size is only however many concurrency slots happened to be
    free. `pool-free` showed it exactly once, on the tick after concurrency went to 2: 63 rounds
    holding one game, and round 115 holding two unrelated fixtures under one heading.
    """
    games = matchmake(FIELD, [], SETTLED, count=2, round_number=115)

    assert [game.round_number for game in games] == [115, 116]


def test_a_single_game_still_takes_the_round_it_was_given() -> None:
    """The common case — one free slot — is unchanged, which is what keeps existing schedules
    readable rather than renumbering them."""
    games = matchmake(FIELD, [], SETTLED, count=1, round_number=115)

    assert [game.round_number for game in games] == [115]


# ============================================================ balance: the greedy round robin


def simulate(
    keys: list[str],
    ticks: int,
    *,
    policy: Policy = Policy.BALANCE,
    slots: int = 1,
    joins: dict[int, list[str]] | None = None,
    unavailable: set[str] | None = None,
) -> list[Pairing]:
    """Run the matchmaker over and over, feeding its own output back as attempts.

    Every pairing is fed back as an *attempt* rather than a result, which is the pool's worst case
    and the honest one: nothing settles, so nothing informs a rating, and a policy that leans on
    ratings has nothing to lean on. It is also exactly what a field of free models does on a bad
    day (ADR-0041).
    """
    field = list(keys)
    attempts: list[Pairing] = []
    for tick in range(ticks):
        for key in (joins or {}).get(tick, []):
            field.append(key)
        entrants = [Entrant(key=k, seed=i) for i, k in enumerate(field, start=1)]
        attempts += matchmake(
            entrants,
            [],
            {},
            count=slots,
            round_number=tick + 1,
            attempts=attempts,
            unavailable=frozenset(unavailable or set()),
            policy=policy,
        )
    return attempts


def pairings_each(games: list[Pairing], field: list[str]) -> dict[str, int]:
    return {key: sum(key in g.pair for g in games) for key in field}


class TestBalance:
    """The policy this exists for: every entrant paired as often as every other, and against
    everybody, over a field that changes while it runs."""

    def test_nobody_gets_a_second_game_before_everybody_has_had_a_first(self) -> None:
        """The 'boring' failure, in one assertion. `INFORMATION` orbits one entrant — in production
        the busiest model appeared in 35% of all games — because nothing in it counts games."""
        field = list("ABCDEF")
        games = simulate(field, ticks=3)

        assert len(games) == 3
        assert sorted(key for game in games for key in game.pair) == field

    def test_the_field_is_covered_before_anything_repeats(self) -> None:
        """A greedy round robin: every pair once before any pair twice."""
        field = list("ABCDEF")
        pairs = len(field) * (len(field) - 1) // 2

        games = simulate(field, ticks=pairs)

        assert len({game.pair for game in games}) == pairs, "every pair, exactly once"

    def test_it_keeps_going_round(self) -> None:
        """A pool never ends, so the round robin simply starts again rather than stalling."""
        field = list("ABCD")
        games = simulate(field, ticks=12)

        counts = collections.Counter(game.pair for game in games)
        assert set(counts.values()) == {2}, "a second full cycle, not a rut"

    def test_pairings_stay_level_across_the_field(self) -> None:
        field = list("ABCDEFG")
        games = simulate(field, ticks=21)

        counts = pairings_each(games, field)
        assert max(counts.values()) - min(counts.values()) <= 1, counts

    def test_a_model_that_never_finishes_a_game_still_takes_its_turn(self) -> None:
        """The reason the count is of **pairings** and not of settled games. Nothing settles in
        this simulation at all; a policy counting results would have nothing to balance on and
        would starve whoever abandons — which is the opposite of what was asked for."""
        field = list("ABCDEF")
        games = simulate(field, ticks=15)

        counts = pairings_each(games, field)
        assert min(counts.values()) >= 4, counts


class TestADynamicField:
    """A pool is not a tournament: entrants arrive and leave with the catalogue, so there is no
    fixture list to freeze and a newcomer must catch up on its own."""

    def test_a_newcomer_is_paired_immediately(self) -> None:
        games = simulate(list("ABCD"), ticks=8, joins={4: ["N"]})

        assert any("N" in game.pair for game in games[4:6]), (
            "a new entrant has no pairings, so it is by definition the furthest behind"
        )

    def test_a_newcomer_catches_up_rather_than_staying_behind(self) -> None:
        field = list("ABCDEF")
        games = simulate(field, ticks=24, joins={6: ["N"]})

        counts = pairings_each(games, [*field, "N"])
        behind = min(counts[k] for k in field) - counts["N"]
        assert behind <= 1, (
            f"a newcomer joining a quarter of the way in is {behind} pairings behind the field; "
            f"it should converge, because zero pairings is top priority — {counts}"
        )

    def test_an_entrant_that_leaves_is_simply_not_paired(self) -> None:
        """Nothing to invalidate — which is the whole reason this is a policy and not a schedule."""
        games = simulate(list("ABCDE"), ticks=10, unavailable={"E"})

        assert all("E" not in game.pair for game in games)


class TestBalanceAgainstInformation:
    """The two policies side by side on one field, because the trade is the decision (ADR-0041)."""

    @pytest.mark.parametrize("policy", list(Policy))
    def test_both_cover_the_pair_before_repeating_it(self, policy: Policy) -> None:
        """The one property they share: an unmet opponent beats a met one either way."""
        games = simulate(list("ABCD"), ticks=6, policy=policy)

        assert len({game.pair for game in games}) == 6

    def test_information_leaves_the_field_lopsided_and_balance_does_not(self) -> None:
        """Measured rather than asserted about. With nothing settling, every entrant keeps its
        default deviation, so `INFORMATION` falls through to rating proximity and the key — and
        returns to the same entrants. This is `pool-free`'s 44% coverage in miniature."""
        field = list("ABCDEFGH")
        spread = {}
        for policy in Policy:
            counts = pairings_each(simulate(field, ticks=12, policy=policy), field)
            spread[policy] = max(counts.values()) - min(counts.values())

        assert spread[Policy.BALANCE] <= 1
        assert spread[Policy.INFORMATION] > spread[Policy.BALANCE]


class TestColoursAlternateEvenWhenGamesDoNotFinish:
    """Colour alternation used to depend on games *settling*, and in this pool many do not.

    The per-entrant balance is built from `Result`s, so a finished game moves it and the next
    meeting of that pair swaps colours on its own — that part always worked and the first two tests
    here are regression cover for it, not the fix.

    **An abandoned game produces no `Result`.** It moves nobody's balance, so the comparison stays
    level and `_colours` fell through to `return home, away` — White to `home`, which under
    `BALANCE` is `min(available, fewest pairings)`. A pairing that keeps being abandoned therefore
    kept being scheduled the same way round, and an entrant whose games never settle kept being
    White. `pool-free` is full of exactly that: five abandoned pairings in the current era, and its
    three least-played entrants are the ones whose endpoints rarely serve.

    The pair ledger counts *attempts* as well as results, which is what closes it.
    """

    def test_a_settled_rematch_already_swapped_the_colours(self) -> None:
        """Regression cover, not the fix: the balance rule has always handled this case."""
        pair = [Entrant(key="A", seed=1), Entrant(key="B", seed=2)]
        first = matchmake(pair, [], {}, count=1)

        played = [
            Result(white=first[0].white, black=first[0].black, white_score=1.0, round_number=1)
        ]
        second = matchmake(pair, played, {}, count=1)

        assert second[0].white == first[0].black
        assert second[0].black == first[0].white

    def test_the_per_entrant_balance_still_wins(self) -> None:
        """The primary rule is unchanged: it is the split that actually biases a rating."""
        pair = [Entrant(key="A", seed=1), Entrant(key="B", seed=2)]
        played = [
            Result(white="A", black="C", white_score=1.0, round_number=1),
            Result(white="A", black="C", white_score=1.0, round_number=2),
        ]

        game = matchmake(pair, played, {}, count=1)[0]

        assert game.black == "A", "the entrant most owed Black must get it"

    def test_an_abandoned_fixture_swaps_the_colours_next_time(self) -> None:
        """The fix. No `Result` exists, so nothing else in the system can tell these apart."""
        pair = [Entrant(key="A", seed=1), Entrant(key="B", seed=2)]
        first = matchmake(pair, [], {}, count=1)

        second = matchmake(pair, [], {}, attempts=first, count=1)

        assert second[0].white == first[0].black, "an abandoned pairing repeated its colours"

    def test_an_entrant_whose_games_never_settle_does_not_collect_whites(self) -> None:
        """The bias this really was, stated over a run rather than a single pairing.

        Nothing settles, so no balance ever moves and every colour decision is a tie. `A` is first
        alphabetically and so wins every tie the old rule had left; with the ledger it alternates.
        """
        pair = [Entrant(key="A", seed=1), Entrant(key="B", seed=2)]
        attempts: list[Pairing] = []

        for _ in range(6):
            attempts = attempts + matchmake(pair, [], {}, attempts=attempts, count=1)

        whites = collections.Counter(game.white for game in attempts)
        assert whites["A"] == 3, f"colours did not alternate across abandoned games: {whites}"
        assert whites["B"] == 3
