"""Reopening a forfeit is gated on the record, never on the flag (ADR-0021, ADR-0022).

`resume_game.py` refuses a forfeit by design: a chess result is a finding about a player, and a
script that can reopen any of them is a script that can replay a bad result until it improves. Two
corrections need it anyway, because two bugs produced forfeits nobody earned — and both are gated
on evidence the operator cannot supply.

* `--harness-ceiling`: a `truncated` forfeit where the **stored calls** show our own `max_tokens`
  cut the response. A miscalculated window asked an endpoint for one output token, every reply came
  back at `length`, and a model was forfeited at ply 5 for a limit it never saw.
* `--overwritten-verdict`: a game whose stored ending replaced an earlier harness stop, written
  when two workers played the same ply at once. One game ended seven times; in another, a race
  turned an excluded `budget_exceeded` into a rated `error_forfeit`.

The refusals matter more than the acceptances, so most of these test those.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType
from chessmark.db.models import Game, LlmCall, Turn
from chessmark.db.repositories import append_event
from chessmark.game import Termination
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


# ====================================================================== our own ceiling


async def test_a_truncation_at_our_ceiling_may_be_reopened(db: AsyncSession, game: Fixture) -> None:
    """The response stopped at exactly what we allowed, so the harness ended it."""
    await _truncated_call(db, game, asked=1, generated=1)

    ok, why = await _resume._truncation_was_our_ceiling(db, await _game(db, game))

    assert ok
    assert "max_tokens (1)" in why


async def test_a_truncation_at_the_providers_ceiling_is_refused(
    db: AsyncSession, game: Fixture
) -> None:
    """Stopping *short* of what we allowed is the endpoint's own limit — a finding that stands."""
    await _truncated_call(db, game, asked=64_000, generated=900)

    ok, why = await _resume._truncation_was_our_ceiling(db, await _game(db, game))

    assert not ok
    assert "stopped short" in why


async def test_a_game_with_no_truncated_call_is_refused(db: AsyncSession, game: Fixture) -> None:
    """The flag must not reopen a game merely because somebody typed it."""
    ok, why = await _resume._truncation_was_our_ceiling(db, await _game(db, game))

    assert not ok
    assert "no truncated call" in why


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


async def test_the_gates_are_independent_of_each_other(db: AsyncSession, game: Fixture) -> None:
    """Each flag answers its own question; neither is a general override."""
    await _truncated_call(db, game, asked=1, generated=1)
    row = await _game(db, game)

    truncation_ok, _ = await _resume._truncation_was_our_ceiling(db, row)
    verdict_ok, _ = await _resume._verdict_was_overwritten(db, row)

    assert truncation_ok
    assert not verdict_ok
