"""Reopening a forfeit is gated on the record, never on the flag (ADR-0022).

`resume_game.py` refuses a forfeit by design: a chess result is a finding about a player, and a
script that can reopen any of them is a script that can replay a bad result until it improves. One
correction needs it anyway, because a bug produced forfeits nobody earned — and it is gated on
evidence the operator cannot supply.

* `--overwritten-verdict`: a game whose stored ending replaced an earlier harness stop, written
  when two workers played the same ply at once. One game ended seven times; in another, a race
  turned an excluded `budget_exceeded` into a rated `error_forfeit`.

**`--harness-ceiling` stood beside it and is gone** (ADR-0024). It reopened a `truncated` forfeit
where the stored calls showed our own `max_tokens` had cut the response, and refused when the
endpoint's own ceiling had. That question no longer has two answers — a truncation is a harness
stop either way — so `TRUNCATED` is in `RESUMABLE_TERMINATIONS` and a plain resume reopens it. The
flag could never have fired again, which is worse than not having one.

The refusals matter more than the acceptances, so most of these test those.
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db import tournaments as tournament_repo
from chessmark.db.enums import EventType, TournamentStatus
from chessmark.db.models import Game, LlmCall, Tournament, TournamentGame, Turn
from chessmark.db.repositories import append_event
from chessmark.game import FORFEIT_TERMINATIONS, RESUMABLE_TERMINATIONS, Termination
from tests.orchestration.conftest import Fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_resume = importlib.import_module("resume_game")

pytestmark = pytest.mark.integration


async def _truncated_call(db: AsyncSession, game: Fixture, *, asked: int, generated: int) -> None:
    turn = Turn(game_id=game.game.id, player_id=game.white.id, status="completed")
    db.add(turn)
    await db.flush()
    db.add(
        LlmCall(
            game_id=game.game.id,
            turn_id=turn.id,
            sequence=1,
            model_slug="scripted/white",
            request={"max_tokens": asked},
            response={},
            finish_reason="length",
            completion_tokens=generated,
        )
    )
    await db.flush()


async def _end(db: AsyncSession, game: Fixture, termination: Termination) -> None:
    await append_event(
        db,
        game_id=game.game.id,
        type=EventType.GAME_ENDED,
        payload={"termination": str(termination), "result": "1-0", "winner": "white"},
    )
    await db.flush()


async def _game(db: AsyncSession, game: Fixture) -> Game:
    return await db.get(Game, game.game.id)  # type: ignore[return-value]


# ============================================================ a truncation needs no gate at all


async def test_a_truncation_is_resumable_without_any_flag(db: AsyncSession, game: Fixture) -> None:
    """What replaced `--harness-ceiling`. The evidence check it was gated on could never fire for
    the failure that mattered: we asked Poolside for 64,000 output tokens against an endpoint that
    stops at 32,768, so every truncated call `stopped short of the ceiling we asked for` and was
    read as the model's doing (ADR-0024)."""
    assert Termination.TRUNCATED in RESUMABLE_TERMINATIONS
    assert Termination.TRUNCATED not in FORFEIT_TERMINATIONS
    assert not hasattr(_resume, "_truncation_was_our_ceiling"), (
        "the gate is gone; a helper nothing can reach is dead code"
    )


# ====================================================================== an overwritten verdict


async def test_a_second_ending_over_a_harness_stop_may_be_reopened(
    db: AsyncSession, game: Fixture
) -> None:
    """The shape of `855e208d`: ended as a harness stop, resurrected, re-ended as a forfeit."""
    await _end(db, game, Termination.BUDGET_EXCEEDED)
    await _end(db, game, Termination.ERROR_FORFEIT)
    row = await _game(db, game)
    row.termination = Termination.ERROR_FORFEIT

    ok, why = await _resume._verdict_was_overwritten(db, row)

    assert ok
    assert "budget_exceeded" in why


async def test_one_ending_is_refused(db: AsyncSession, game: Fixture) -> None:
    """A game that ended once has no overwritten verdict, whatever the operator believes."""
    await _end(db, game, Termination.ERROR_FORFEIT)
    row = await _game(db, game)
    row.termination = Termination.ERROR_FORFEIT

    ok, why = await _resume._verdict_was_overwritten(db, row)

    assert not ok
    assert "ended once" in why


async def test_a_first_ending_that_was_a_forfeit_is_refused(
    db: AsyncSession, game: Fixture
) -> None:
    """**The point of the whole gate.** Two endings are not licence to pick the better one: if the
    race overwrote a *forfeit*, the finding stands and the game stays closed."""
    await _end(db, game, Termination.ILLEGAL_MOVE_FORFEIT)
    await _end(db, game, Termination.ERROR_FORFEIT)
    row = await _game(db, game)
    row.termination = Termination.ERROR_FORFEIT

    ok, why = await _resume._verdict_was_overwritten(db, row)

    assert not ok
    assert "finding about a player" in why


async def test_a_verdict_that_already_matches_the_first_is_refused(
    db: AsyncSession, game: Fixture
) -> None:
    """Nothing to restore, so nothing to reopen — and a second run must not undo the first."""
    await _end(db, game, Termination.BUDGET_EXCEEDED)
    await _end(db, game, Termination.BUDGET_EXCEEDED)
    row = await _game(db, game)
    row.termination = Termination.BUDGET_EXCEEDED

    ok, why = await _resume._verdict_was_overwritten(db, row)

    assert not ok
    assert "already the first one written" in why


async def test_the_gate_is_not_a_general_override(db: AsyncSession, game: Fixture) -> None:
    """It answers its own question and nothing else: a game with a truncated call but only one
    ending is not an overwritten verdict."""
    await _truncated_call(db, game, asked=1, generated=1)

    verdict_ok, _ = await _resume._verdict_was_overwritten(db, await _game(db, game))

    assert not verdict_ok


# ====================================================================== a forfeit a resume outgrew


async def test_reopening_a_harness_stop_clears_the_seats_forfeit(
    db: AsyncSession, game: Fixture
) -> None:
    """The flag is a verdict written by the ending being reopened, and it is published — it is the
    leaderboard's forfeits column (ADR-0024).

    Two free-pool games were stopped by `budget_exceeded`, reopened, and played on to a real
    checkmate and a real threefold draw. Both endings were correct, both games stayed ratable, and
    both models carried a forfeit nothing in their play had earned.
    """
    game.white.forfeited = True
    await db.flush()

    cleared = await _resume._clear_stale_forfeits(
        db, await _game(db, game), Termination.BUDGET_EXCEEDED
    )

    assert cleared == 1
    await db.refresh(game.white)
    assert not game.white.forfeited


async def test_a_seat_that_never_forfeited_is_left_alone(db: AsyncSession, game: Fixture) -> None:
    """Nothing to clear, and it must not report that it cleared something."""
    assert (
        await _resume._clear_stale_forfeits(db, await _game(db, game), Termination.ABANDONED) == 0
    )


async def test_a_genuine_forfeit_is_never_cleared(db: AsyncSession, game: Fixture) -> None:
    """No resumable ending is a forfeit today, so this cannot fire — which is exactly why it is
    asserted. Clearing the flag on a real forfeit would erase the finding rather than a mistake,
    and the guard is what keeps that true if `RESUMABLE_TERMINATIONS` ever grows."""
    game.white.forfeited = True
    await db.flush()

    cleared = await _resume._clear_stale_forfeits(
        db, await _game(db, game), Termination.ILLEGAL_MOVE_FORFEIT
    )

    assert cleared == 0
    await db.refresh(game.white)
    assert game.white.forfeited


# ============================================================ a pairing a resume must un-settle


async def _pairing(db: AsyncSession, game: Fixture, **verdict: object) -> TournamentGame:
    """A pool pairing pointing at this game, carrying whatever verdict the caller names."""
    tournament = Tournament(
        slug=f"pool-{uuid.uuid4().hex[:8]}",
        name="reopening",
        format="pool",
        status=TournamentStatus.RUNNING,
        rounds=1,
        max_concurrent=1,
    )
    db.add(tournament)
    await db.flush()

    row = TournamentGame(
        tournament_id=tournament.id,
        era=tournament_repo.current_era(),
        round_number=1,
        white_key="vendor/white",
        black_key="vendor/black",
        game_id=game.game.id,
        started_at=dt.datetime.now(dt.UTC),
        ended_at=dt.datetime.now(dt.UTC),
        **verdict,
    )
    db.add(row)
    await db.flush()
    return row


async def test_reopening_a_game_clears_the_score_its_pairing_holds(
    db: AsyncSession, game: Fixture
) -> None:
    """`pool-free` round 175, and the reason this function now exists apart from `main`.

    A 300-ply cap drew a game White was winning — a pawn on g7 and `g8=Q` on the move — and the
    pairing recorded `0.5` for both seats. Reopening it has to take that back: the verdict came
    from our own ceiling, and until the game reaches a new ending its pairing is *in flight*, which
    is `white_score IS NULL` (the column's own comment).
    """
    row = await _pairing(db, game, white_score=0.5)

    was = await _resume._unsettle_pairing(db, await _game(db, game))

    assert was == "scored 0.5"
    await db.refresh(row)
    assert row.white_score is None
    assert row.ended_at is None


async def test_reopening_a_game_clears_the_abandonment_its_pairing_holds(
    db: AsyncSession, game: Fixture
) -> None:
    """The other half a pairing can carry. A game abandoned on a provider 404 was resumed and
    played on to checkmate at ply 120, and its pairing stayed *abandoned, no score* for ever."""
    row = await _pairing(db, game, abandoned_reason="provider returned 404")

    was = await _resume._unsettle_pairing(db, await _game(db, game))

    assert was == "abandoned"
    await db.refresh(row)
    assert row.abandoned_reason is None
    assert row.white_score is None
    assert row.ended_at is None


async def test_a_score_is_cleared_even_when_no_abandonment_is_recorded(
    db: AsyncSession, game: Fixture
) -> None:
    """**The regression this file exists to stop repeating.**

    The first version cleared `abandoned_reason` alone, so a pairing holding only a score kept it:
    four resumed games ran for up to 89 plies while the schedule drew them as **played**, carrying
    the score of the forfeit that had just been overturned, and the event reported `live: 0` with
    four boards moving. Asserted as its own case because it passes trivially when both columns are
    set — which is how it survived the test that did exist.
    """
    row = await _pairing(db, game, white_score=1.0)
    assert row.abandoned_reason is None

    await _resume._unsettle_pairing(db, await _game(db, game))

    await db.refresh(row)
    assert row.white_score is None


async def test_a_pairing_with_nothing_recorded_is_left_alone(
    db: AsyncSession, game: Fixture
) -> None:
    """Nothing to take back, and it must not report that it took something back — the caller
    prints its answer, and a resume that claims to have re-opened a pairing it never touched sends
    an operator looking for a change that did not happen."""
    await _pairing(db, game)

    assert await _resume._unsettle_pairing(db, await _game(db, game)) is None


async def test_a_game_in_no_tournament_is_not_an_error(db: AsyncSession, game: Fixture) -> None:
    """Most resumes are of a game nobody paired. The lookup returning nothing is the ordinary
    case, not a failure, and a human game must be reopenable without a tournament row existing."""
    assert await _resume._unsettle_pairing(db, await _game(db, game)) is None
