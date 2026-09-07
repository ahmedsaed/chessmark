"""`resume` takes the id the site actually shows you.

Every id a person reads off this project is abbreviated. The replay header prints `game 9b372624`,
the worker logs `abandoning 9b372624-… at ply 18`, and the schedule links by the same eight
characters — and then the command to act on one demanded all thirty-six, which meant going back to
a URL or a database for something already on screen.

The refusals are the interesting half. A resume is a state change on a record the leaderboard
reads, so an ambiguous prefix is refused and listed rather than resolved to whichever row came
back first.
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.models import Game
from tests.orchestration.conftest import Fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_resume = importlib.import_module("resume_game")

pytestmark = pytest.mark.integration


async def _game_with_id(db: AsyncSession, game_id: uuid.UUID, *, start_fen: str) -> Game:
    """A second game whose id we choose, so a prefix can be made to collide on purpose."""
    twin = Game(id=game_id, start_fen=start_fen)
    db.add(twin)
    await db.commit()
    return twin


async def test_the_full_id_still_works(db: AsyncSession, game: Fixture) -> None:
    """The existing contract, unchanged. Every runbook and every log line pastes a full id."""
    assert await _resume.resolve_game_id(db, str(game.game.id)) == game.game.id


async def test_the_first_eight_characters_are_enough(db: AsyncSession, game: Fixture) -> None:
    """What the site prints, and what an operator has in front of them."""
    short = str(game.game.id)[:8]

    assert await _resume.resolve_game_id(db, short) == game.game.id


async def test_a_prefix_may_span_the_hyphen(db: AsyncSession, game: Fixture) -> None:
    """Ids are copied in their canonical hyphenated form, so a prefix of one contains hyphens.
    Matching against the rendered text rather than the raw bytes is what makes that work without a
    special case."""
    assert await _resume.resolve_game_id(db, str(game.game.id)[:13]) == game.game.id


async def test_it_is_case_insensitive(db: AsyncSession, game: Fixture) -> None:
    """A UUID reads the same in either case and gets pasted in both."""
    assert await _resume.resolve_game_id(db, str(game.game.id)[:8].upper()) == game.game.id


async def test_an_ambiguous_prefix_is_refused_and_listed(db: AsyncSession, game: Fixture) -> None:
    """**The refusal that matters.** Two games sharing a prefix must not resolve to whichever row
    the database returned first — reopening the wrong game is worse than typing a full id.
    """
    twin = uuid.UUID(str(game.game.id)[:9] + "0000-0000-0000-000000000000"[:27])
    await _game_with_id(db, twin, start_fen=game.game.start_fen)
    shared = str(game.game.id)[:9]

    with pytest.raises(SystemExit) as refusal:
        await _resume.resolve_game_id(db, shared)

    message = str(refusal.value)
    assert "matches 2 games" in message
    assert str(game.game.id) in message and str(twin) in message, (
        "both are named, so a person can pick"
    )


async def test_a_prefix_too_short_to_trust_is_refused(db: AsyncSession, game: Fixture) -> None:
    """Not because it is ambiguous today — it may well not be — but because it stops being unique
    exactly as the event grows, and a command that silently starts matching a different game as the
    pool fills is not a convenience."""
    with pytest.raises(SystemExit) as refusal:
        await _resume.resolve_game_id(db, str(game.game.id)[:4])

    assert "too short" in str(refusal.value)


async def test_a_prefix_matching_nothing_says_so(db: AsyncSession) -> None:
    """Distinct from "ambiguous", because the fix is different: one is a typo, the other needs more
    characters."""
    with pytest.raises(SystemExit) as refusal:
        await _resume.resolve_game_id(db, "ffffffff-dead-beef")

    assert "no game" in str(refusal.value)


async def test_the_floor_is_the_length_the_site_prints(db: AsyncSession) -> None:
    """Asserted because it is a decision, not an implementation detail: eight is what `game.id[:8]`
    renders everywhere a person reads an id."""
    assert _resume.MIN_PREFIX == 8


def test_a_paused_game_is_told_it_has_not_ended() -> None:
    """A paused game has not ended, so there is nothing to reopen.

    It used to fall through to the resumable check and be refused for having "ended by `None`" —
    true, and it reads as a broken record rather than as a game still alive and waiting on a
    provider. An operator seeing that goes debugging instead of waiting.
    """
    from chessmark.db.enums import GameStatus

    paused = SimpleNamespace(
        status=GameStatus.PAUSED,
        pause_reason="poolside/laguna-xs-2.1:free rate-limited by Poolside",
        resume_after=None,
    )

    message = _resume._paused_refusal(paused)

    assert message is not None
    assert "paused, not ended" in message
    assert "rate-limited by Poolside" in message, "the reason it waits, not merely that it does"
    assert "next tick" in message, "and that it needs no help — the reconciler has it"


def test_a_paused_game_still_waiting_says_how_long() -> None:
    """The other half of the decision: nothing to do *yet* is different from nothing to do."""
    from chessmark.db.enums import GameStatus

    soon = SimpleNamespace(
        status=GameStatus.PAUSED,
        pause_reason="rate-limited",
        resume_after=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=42),
    )

    message = _resume._paused_refusal(soon)

    assert message is not None and "about 42 minutes" in message


def test_an_ended_game_is_not_refused_for_being_paused() -> None:
    """The guard must not swallow the real path: an abandoned game is exactly what `resume` is
    for."""
    from chessmark.db.enums import GameStatus

    ended = SimpleNamespace(status=GameStatus.ABORTED, pause_reason=None, resume_after=None)

    assert _resume._paused_refusal(ended) is None
