"""A pairing scores a game only when the game is a finding about the players (invariant 11).

`bench/ratable.py` has said which endings those are since Phase 12, and `settle` said the same
thing in prose — "a game the harness stopped ... is marked abandoned rather than scored" — while
asking a question that could not answer it. It read `GameStatus.ABORTED`, and only `ABANDONED`
arrives that way. `PLY_CAP`, `BUDGET_EXCEEDED` and `ADJUDICATION` end a game `FINISHED`, carrying a
real `GameResult`, so they fell through to `_SCORES` and were scored like any other draw.

The live instance is `pool-free` round 175. `ling-3.0-flash-sante` had a pawn on g7 and `g8=Q` on
the move against a king on b6 — `8/6P1/1k5P/5K2/5p2/8/8/8 w` — and the 300-ply cap drew it. The
rating excluded it and the pool's table did not, so the page showed `0.5` beside `unrated` and
`lfm-2.5-2.6b` carried half a point our own ceiling had given it.

This is the fourth set of terminations, and the reason `test_classification.py` exists is that the
first three drifted apart once. The last test here is the link.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.bench.ratable import HARNESS_TERMINATIONS, RATED_TERMINATIONS
from chessmark.db import tournaments as repo
from chessmark.db.enums import GameStatus, TournamentStatus
from chessmark.db.models import Game, Tournament, TournamentGame
from chessmark.game import GameResult, Termination

pytestmark = pytest.mark.integration


async def _pairing(db: AsyncSession) -> TournamentGame:
    """One pairing of one pool, with nothing recorded against it yet."""
    tournament = Tournament(
        slug=f"pool-{uuid.uuid4().hex[:8]}",
        name="settling",
        format="pool",
        status=TournamentStatus.RUNNING,
        rounds=1,
        max_concurrent=1,
    )
    db.add(tournament)
    await db.flush()

    row = TournamentGame(
        tournament_id=tournament.id,
        era=repo.current_era(),
        round_number=1,
        white_key="vendor/white",
        black_key="vendor/black",
        started_at=dt.datetime.now(dt.UTC),
    )
    db.add(row)
    await db.flush()
    return row


def _stopped(termination: Termination, *, result: GameResult = GameResult.DRAW) -> Game:
    """A game the harness ended. Transient on purpose — `settle` only reads it, and keeping it out
    of the session keeps this test about the rule rather than about a fixture."""
    return Game(
        id=uuid.uuid4(),
        status=GameStatus.FINISHED,
        result=result,
        termination=termination,
        termination_detail=f"stopped: {termination}",
    )


async def test_the_ply_cap_is_not_scored(db: AsyncSession) -> None:
    """Round 175, in one assertion. Without the fix this records 0.5 for each seat."""
    row = await _pairing(db)

    changed = await repo.settle(db, row, _stopped(Termination.PLY_CAP))

    assert changed is False
    assert row.white_score is None
    assert row.abandoned_reason == f"stopped: {Termination.PLY_CAP}"


async def test_a_harness_stop_is_left_out_of_the_standings(db: AsyncSession) -> None:
    """The consequence, at the level a reader sees it. `results_so_far` feeds the pool's points and
    W/D/L columns, and an unscored pairing must not reach them — which is the same rule
    `attempted` deliberately does *not* apply, because "do not score it" and "pretend it never
    happened" are different instructions."""
    row = await _pairing(db)
    await repo.settle(db, row, _stopped(Termination.PLY_CAP))

    assert await repo.results_so_far(db, row.tournament_id) == []
    assert [(p.white, p.black) for p in await repo.attempted(db, row.tournament_id)] == [
        ("vendor/white", "vendor/black")
    ]


async def test_a_real_result_is_still_scored(db: AsyncSession) -> None:
    """The other half. Fixing this by refusing to score draws, or finished games, would empty the
    table — an insufficient-material draw is a real half point and must stay one."""
    row = await _pairing(db)

    changed = await repo.settle(db, row, _stopped(Termination.INSUFFICIENT_MATERIAL))

    assert changed is True
    assert row.white_score == 0.5
    assert row.abandoned_reason is None


async def test_a_forfeit_is_still_scored(db: AsyncSession) -> None:
    """A forfeit is the benchmark's whole subject: a model that cannot operate its tools has lost,
    and sweeping it in with the harness stops would make the table flatter and less true."""
    row = await _pairing(db)

    changed = await repo.settle(
        db, row, _stopped(Termination.ILLEGAL_MOVE_FORFEIT, result=GameResult.BLACK_WINS)
    )

    assert changed is True
    assert row.white_score == 0.0
    assert row.abandoned_reason is None


async def test_a_resume_that_reaches_a_real_result_overrides_the_abandonment(
    db: AsyncSession,
) -> None:
    """A ply-cap game reopened with a higher cap and played to mate must settle as the mate. The
    two cannot both be true, and the game record is the authority (invariant 1) — this is the path
    that makes resuming `pool-free` round 175 a real result rather than a permanent asterisk."""
    row = await _pairing(db)
    await repo.settle(db, row, _stopped(Termination.PLY_CAP))
    assert row.abandoned_reason is not None

    changed = await repo.settle(
        db, row, _stopped(Termination.CHECKMATE, result=GameResult.WHITE_WINS)
    )

    assert changed is True
    assert row.white_score == 1.0
    assert row.abandoned_reason is None


async def test_settling_is_idempotent_for_a_harness_stop(db: AsyncSession) -> None:
    """The runner offers every pairing on every tick, so a second call must not report a change
    and must not keep rewriting `ended_at`."""
    row = await _pairing(db)
    await repo.settle(db, row, _stopped(Termination.PLY_CAP))

    assert await repo.settle(db, row, _stopped(Termination.PLY_CAP)) is False
    assert row.white_score is None


@pytest.mark.parametrize("termination", sorted(HARNESS_TERMINATIONS))
async def test_no_harness_ending_is_ever_scored(db: AsyncSession, termination: Termination) -> None:
    """**The link.** `settle` is the fourth set of terminations in this codebase and was the one
    nothing checked; asking `HARNESS_TERMINATIONS` directly is what keeps it from drifting again.
    Parametrised over the set rather than over a list written here, so adding a member cannot
    quietly leave this behind."""
    row = await _pairing(db)

    await repo.settle(db, row, _stopped(termination))

    assert row.white_score is None, f"{termination} was scored"
    assert row.abandoned_reason is not None


@pytest.mark.parametrize("termination", sorted(RATED_TERMINATIONS))
async def test_every_rated_ending_is_scored(db: AsyncSession, termination: Termination) -> None:
    """And the mirror, so the fix cannot be "abandon everything". A termination the rating counts
    must produce a score, or the table and the leaderboard would disagree the other way."""
    row = await _pairing(db)

    await repo.settle(db, row, _stopped(termination))

    assert row.white_score == 0.5, f"{termination} was not scored"
    assert row.abandoned_reason is None


async def test_a_game_still_in_flight_is_not_settled(db: AsyncSession) -> None:
    """`termination` is null while a game is running, and null is not in either set. A running game
    that started scoring itself is what once drew four live boards as **played**."""
    row = await _pairing(db)
    running = Game(id=uuid.uuid4(), status=GameStatus.RUNNING, result=GameResult.ONGOING)

    assert await repo.settle(db, row, running) is False
    assert row.white_score is None
    assert row.abandoned_reason is None


async def test_an_aborted_game_is_still_abandoned_by_status(db: AsyncSession) -> None:
    """`ABANDONED` arrives as `ABORTED` and has no result at all, so the status check stays: the
    termination test is an addition to it, not a replacement."""
    row = await _pairing(db)
    aborted = Game(
        id=uuid.uuid4(),
        status=GameStatus.ABORTED,
        result=GameResult.ONGOING,
        termination=Termination.ABANDONED,
        termination_detail="the provider never answered",
    )

    assert await repo.settle(db, row, aborted) is False
    assert row.white_score is None
    assert row.abandoned_reason == "the provider never answered"


async def test_the_pairing_row_survives_a_flush(db: AsyncSession) -> None:
    """`settle` flushes, so the verdict is readable by the same transaction that wrote it — the
    property `advance` relies on when it settles and then ranks in one tick."""
    row = await _pairing(db)
    await repo.settle(db, row, _stopped(Termination.PLY_CAP))

    stored = await db.scalar(sa.select(TournamentGame).where(TournamentGame.id == row.id))

    assert stored is not None
    assert stored.white_score is None
    assert stored.abandoned_reason is not None
