"""Glicko-2, checked against Glickman's own worked example.

The exit criterion for Phase 12, and the reason the system is implemented here rather than
installed: a library that happened to agree would say nothing about our arithmetic. The example is
from "Example of the Glicko-2 system" (Glickman, 2013), which walks a single player through one
rating period and prints every intermediate value.

The rest of the file is about the properties a leaderboard depends on — that uncertainty shrinks
with evidence and grows with absence, that beating a strong opponent is worth more than beating a
weak one, and that the whole thing is deterministic.
"""

from __future__ import annotations

import pytest

from chessmark.bench.glicko2 import (
    DEFAULT_RATING,
    DEFAULT_RD,
    DEFAULT_VOLATILITY,
    PROVISIONAL_RD,
    Glicko2,
    Outcome,
    Rating,
)

# ====================================================================== the paper


def test_the_worked_example_reproduces_the_papers_computed_values() -> None:
    """Glickman's example in full.

    A player at 1500 (RD 200, sigma 0.06), tau = 0.5, playing three opponents: beating 1400 (RD 30),
    losing to 1550 (RD 100), losing to 1700 (RD 300).

    **Asserted on the internal scale, because that is what the paper actually computes.** It prints
    mu' = -0.2069 and phi' = 0.8722, and we reproduce both exactly at that precision.

    The paper's headline `r' = 1464.06` is *not* what full-precision arithmetic gives. It converts
    its own rounded mu': -0.2069 * 173.7178 + 1500 = 1464.0578, printed as 1464.06. Carrying full
    precision through gives 1464.0507. The difference is the paper's rounding, not our error, and
    asserting 1464.06 would mean reproducing a display artifact rather than the algorithm.
    """
    system = Glicko2(tau=0.5)
    player = Rating(rating=1500, rd=200, volatility=0.06)

    updated = system.rate(
        player,
        [
            Outcome(Rating(rating=1400, rd=30), score=1.0),
            Outcome(Rating(rating=1550, rd=100), score=0.0),
            Outcome(Rating(rating=1700, rd=300), score=0.0),
        ],
    )

    # The paper's own values, at the paper's own precision.
    assert round(updated.mu, 4) == -0.2069
    assert round(updated.phi, 4) == 0.8722

    # sigma' is printed as 0.05999 — truncated, not rounded; 0.0599959... rounds to 0.06000.
    assert round(updated.volatility, 6) == 0.059996

    # And the full-precision conversion back, to well beyond three decimal places.
    assert round(updated.rating, 4) == 1464.0507
    assert round(updated.rd, 4) == 151.5165


def test_the_papers_printed_rating_is_recovered_from_its_rounded_intermediate() -> None:
    """Pins the explanation above, so nobody later "fixes" the numbers to match the PDF.

    Rounding mu' to the four decimals the paper prints, then converting, reproduces its 1464.06.
    """
    system = Glicko2(tau=0.5)
    updated = system.rate(
        Rating(rating=1500, rd=200, volatility=0.06),
        [
            Outcome(Rating(rating=1400, rd=30), score=1.0),
            Outcome(Rating(rating=1550, rd=100), score=0.0),
            Outcome(Rating(rating=1700, rd=300), score=0.0),
        ],
    )

    as_the_paper_prints_it = round(updated.mu, 4) * 173.7178 + 1500

    assert round(as_the_paper_prints_it, 2) == 1464.06
    assert round(round(updated.phi, 4) * 173.7178, 2) == 151.52


def test_the_internal_scale_matches_the_paper() -> None:
    """μ and φ for the example player: 0 and 1.1513."""
    player = Rating(rating=1500, rd=200)

    assert round(player.mu, 4) == 0.0
    assert round(player.phi, 4) == 1.1513


def test_the_scale_round_trips() -> None:
    player = Rating(rating=1673.4, rd=88.2, volatility=0.052)
    restored = Rating.from_internal(player.mu, player.phi, player.volatility)

    assert round(restored.rating, 6) == 1673.4
    assert round(restored.rd, 6) == 88.2


# ====================================================================== what a leaderboard needs


def test_a_new_contestant_starts_maximally_uncertain() -> None:
    """The default RD is the whole point: 1500 ± 500 says "we know nothing", and a leaderboard that
    prints it next to 1500 ± 40 is telling the truth about both."""
    new = Rating()

    assert new.rating == DEFAULT_RATING
    assert new.rd == DEFAULT_RD
    assert new.volatility == DEFAULT_VOLATILITY


def test_the_prior_is_wider_than_glickmans_350() -> None:
    """Asserted as a policy rather than an implementation detail. 350 is calibrated for a pool where
    a new player is rare among many settled ones; ours is the opposite — the matchmaker deliberately
    pairs whoever is least known, so most of what we spend is spent on models that have barely
    played, and the first few games should be allowed to say more. Lichess's number, for the same
    reason."""
    assert DEFAULT_RD == 500.0


def test_a_new_rating_is_provisional_and_a_settled_one_is_not() -> None:
    """`± 208` is honest and most readers cannot act on it. This is the same fact in a word."""
    assert Rating().provisional
    assert Rating(rd=PROVISIONAL_RD + 0.1).provisional
    assert not Rating(rd=PROVISIONAL_RD).provisional, "the threshold itself is settled"
    assert not Rating(rd=40).provisional


def test_the_provisional_threshold_is_lichesss_not_ours() -> None:
    """Adopted verbatim rather than tuned. A threshold picked to make our own table look settled
    would be worth nothing — and today it does the opposite: every contestant in `pool-free` sits
    between 150 and 265 over two to nine games, so every one of them is provisional, which is the
    correct thing for the page to say."""
    assert PROVISIONAL_RD == 110.0


def test_games_settle_a_rating_out_of_provisional() -> None:
    """Or the flag would be permanent, which is a different way of saying nothing."""
    system = Glicko2()
    player = Rating()

    for _ in range(12):
        player = system.rate(player, [Outcome(Rating(rating=1500, rd=60), score=0.5)])

    assert not player.provisional


def test_playing_games_reduces_uncertainty() -> None:
    """Evidence narrows the interval. Without this the rating never becomes worth reading."""
    system = Glicko2()
    player = Rating()

    after = system.rate(player, [Outcome(Rating(), score=1.0) for _ in range(5)])

    assert after.rd < player.rd


def test_a_period_without_games_widens_uncertainty() -> None:
    """Not a formality. A model that played once in March and is still shown at ± 60 in December is
    a claim the leaderboard cannot support."""
    system = Glicko2()
    settled = Rating(rating=1600, rd=50, volatility=0.06)

    idle = system.rate(settled, [])

    assert idle.rating == pytest.approx(settled.rating)
    assert idle.rd > settled.rd


def test_absence_compounds() -> None:
    system = Glicko2()
    rating = Rating(rating=1600, rd=50)

    one = system.rate(rating, [])
    two = system.rate(one, [])

    assert two.rd > one.rd


def test_beating_a_stronger_opponent_gains_more() -> None:
    """The property that makes a rating a ranking rather than a win count."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)

    over_weak = system.rate(player, [Outcome(Rating(rating=1200, rd=50), score=1.0)])
    over_strong = system.rate(player, [Outcome(Rating(rating=1800, rd=50), score=1.0)])

    assert over_strong.rating > over_weak.rating


def test_losing_to_a_weaker_opponent_costs_more() -> None:
    system = Glicko2()
    player = Rating(rating=1500, rd=200)

    to_weak = system.rate(player, [Outcome(Rating(rating=1200, rd=50), score=0.0)])
    to_strong = system.rate(player, [Outcome(Rating(rating=1800, rd=50), score=0.0)])

    assert to_weak.rating < to_strong.rating


def test_an_uncertain_opponent_moves_the_needle_less() -> None:
    """Beating someone nobody has measured is weak evidence, and the system says so."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)

    over_known = system.rate(player, [Outcome(Rating(rating=1800, rd=30), score=1.0)])
    over_unknown = system.rate(player, [Outcome(Rating(rating=1800, rd=350), score=1.0)])

    assert over_known.rating > over_unknown.rating


def test_a_draw_sits_between_a_win_and_a_loss() -> None:
    system = Glicko2()
    player = Rating(rating=1500, rd=200)
    opponent = Rating(rating=1500, rd=200)

    win = system.rate(player, [Outcome(opponent, score=1.0)])
    draw = system.rate(player, [Outcome(opponent, score=0.5)])
    loss = system.rate(player, [Outcome(opponent, score=0.0)])

    assert loss.rating < draw.rating < win.rating


def test_a_draw_between_equals_barely_moves_the_rating() -> None:
    """It is the expected result, so it is nearly no information about who is better."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)

    drawn = system.rate(player, [Outcome(Rating(rating=1500, rd=200), score=0.5)])

    assert abs(drawn.rating - 1500) < 1.0
    assert drawn.rd < 200, "even an uninformative game tells us something about the spread"


# ====================================================================== determinism


def test_the_same_input_gives_the_same_answer() -> None:
    """The determinism criterion in miniature: recomputing from scratch must reproduce the stored
    value exactly, and that is only possible if this is a pure function of its arguments."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)
    outcomes = [
        Outcome(Rating(rating=1400, rd=30), score=1.0),
        Outcome(Rating(rating=1550, rd=100), score=0.0),
    ]

    first = system.rate(player, outcomes)
    second = system.rate(player, outcomes)

    assert first == second


def test_the_order_of_games_within_a_period_does_not_matter() -> None:
    """Glicko-2 rates a *period*, not a sequence. If order mattered, the rating would depend on
    which order we happened to read rows out of the database."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)
    outcomes = [
        Outcome(Rating(rating=1400, rd=30), score=1.0),
        Outcome(Rating(rating=1550, rd=100), score=0.0),
        Outcome(Rating(rating=1700, rd=300), score=0.0),
    ]

    forward = system.rate(player, outcomes)
    backward = system.rate(player, list(reversed(outcomes)))

    assert round(forward.rating, 9) == round(backward.rating, 9)
    assert round(forward.rd, 9) == round(backward.rd, 9)


def test_rating_a_period_is_not_the_same_as_rating_game_by_game() -> None:
    """Worth pinning, because doing it per game is the obvious shortcut and gives a different —
    and less defensible — answer than the system specifies."""
    system = Glicko2()
    player = Rating(rating=1500, rd=200)
    outcomes = [
        Outcome(Rating(rating=1400, rd=30), score=1.0),
        Outcome(Rating(rating=1550, rd=100), score=0.0),
    ]

    batched = system.rate(player, outcomes)

    one_at_a_time = player
    for outcome in outcomes:
        one_at_a_time = system.rate(one_at_a_time, [outcome])

    assert round(batched.rating, 3) != round(one_at_a_time.rating, 3)


# ====================================================================== the solver


def test_volatility_stays_finite_under_a_shock() -> None:
    """A huge upset drives the volatility solver into its awkward branch. Plain regula falsi stalls
    there; the Illinois halving is what stops a rating silently never converging."""
    system = Glicko2()
    player = Rating(rating=1500, rd=30, volatility=0.06)

    shocked = system.rate(player, [Outcome(Rating(rating=2800, rd=30), score=1.0)])

    assert 0.0 < shocked.volatility < 1.0
    assert shocked.rating > 1500


def test_a_long_unbeaten_run_does_not_break_the_solver() -> None:
    system = Glicko2()
    rating = Rating()

    for _ in range(30):
        rating = system.rate(rating, [Outcome(Rating(rating=1500, rd=100), score=1.0)])

    assert rating.rating > 1500
    assert 0.0 < rating.volatility < 1.0
    assert rating.rd > 0
