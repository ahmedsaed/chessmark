"""Re-ending a game the harness scored against the wrong party (invariant 11, ADR-0015).

**Over the write path, which is the whole point.** The first version of this script was checked by
running its dry run, which reads the game and prints a report and touches none of the code that
changes anything — so `GameResult.UNFINISHED`, a name that has never existed, sat two lines inside
the `--write` branch and reached production. All three repairs failed on it.

A dry run is not a rehearsal of the write; it is the branch that skips it.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game, GameEvent
from chessmark.game import GameResult, Termination

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_repair = importlib.import_module("repair_verdict")

pytestmark = pytest.mark.integration


async def _forfeited(db: AsyncSession) -> Game:
    """A finished game carrying a verdict about a model."""
    game = Game(
        status=GameStatus.FINISHED,
        result=GameResult.BLACK_WINS,
        termination=Termination.ERROR_FORFEIT,
        winner_colour="black",
        ply_count=74,
        start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    )
    db.add(game)
    await db.flush()
    return game


async def _run(db: AsyncSession, monkeypatch: Any, *argv: str) -> int:
    """The script's `main`, against the test session rather than a real one."""

    class _Sessionmaker:
        def __call__(self) -> Any:
            return _Session()

    class _Session:
        async def __aenter__(self) -> AsyncSession:
            return db

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(_repair, "get_sessionmaker", lambda: _Sessionmaker())
    monkeypatch.setattr(_repair, "dispose_engine", _noop)
    monkeypatch.setattr(db, "begin", _nested(db))
    monkeypatch.setattr(sys, "argv", ["repair_verdict.py", *argv])
    return await _repair.main()


async def _noop() -> None:
    return None


def _nested(db: AsyncSession) -> Any:
    """`session.begin()` on a session already inside the test's transaction would fail; the test
    fixture owns the outer one, so this nests instead."""
    return db.begin_nested


class TestTheWritePath:
    async def test_the_game_is_re_ended_as_abandoned(
        self, db: AsyncSession, monkeypatch: Any
    ) -> None:
        """`abandoned` because it is already the classification for "we could not get a result
        through", and `HARNESS_TERMINATIONS` excludes it from every rating."""
        game = await _forfeited(db)

        code = await _run(
            db, monkeypatch, str(game.id), "--reason", "the endpoint, not the model", "--write"
        )

        assert code == 0
        await db.refresh(game)
        assert game.termination is Termination.ABANDONED
        assert game.result is GameResult.ONGOING
        assert game.winner_colour is None
        assert game.status is GameStatus.ABORTED

    async def test_the_correction_is_appended_not_substituted(
        self, db: AsyncSession, monkeypatch: Any
    ) -> None:
        """**The property that makes it checkable.** A second `game_ended` says what the ending
        should have been and names the one it corrects, so the log holds both in order and a
        reader can judge the correction against the original rather than take it on trust."""
        game = await _forfeited(db)

        await _run(
            db, monkeypatch, str(game.id), "--reason", "the endpoint, not the model", "--write"
        )

        events = list(
            await db.scalars(
                sa.select(GameEvent)
                .where(GameEvent.game_id == game.id, GameEvent.type == EventType.GAME_ENDED)
                .order_by(GameEvent.seq)
            )
        )
        assert len(events) == 1
        assert events[-1].payload["termination"] == str(Termination.ABANDONED)
        assert events[-1].payload["corrects"] == "error_forfeit"
        assert "the endpoint, not the model" in events[-1].payload["detail"]

    async def test_a_dry_run_changes_nothing(self, db: AsyncSession, monkeypatch: Any) -> None:
        game = await _forfeited(db)

        code = await _run(db, monkeypatch, str(game.id), "--reason", "reporting only")

        assert code == 0
        await db.refresh(game)
        assert game.termination is Termination.ERROR_FORFEIT


class TestWhatItRefuses:
    async def test_a_game_that_is_still_running(self, db: AsyncSession, monkeypatch: Any) -> None:
        """Re-ending a live game would take a result away from a game that has not produced one."""
        game = Game(
            status=GameStatus.RUNNING,
            start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        )
        db.add(game)
        await db.flush()

        assert await _run(db, monkeypatch, str(game.id), "--reason", "x", "--write") == 1

    async def test_a_game_already_abandoned(self, db: AsyncSession, monkeypatch: Any) -> None:
        """Running it twice must be a no-op, not a second correction stacked on the first."""
        game = await _forfeited(db)
        game.termination = Termination.ABANDONED
        await db.flush()

        code = await _run(db, monkeypatch, str(game.id), "--reason", "x", "--write")

        assert code == 0
        events = list(await db.scalars(sa.select(GameEvent).where(GameEvent.game_id == game.id)))
        assert events == []

    async def test_an_unknown_game(self, db: AsyncSession, monkeypatch: Any) -> None:
        assert await _run(db, monkeypatch, str(uuid.uuid4()), "--reason", "x", "--write") == 1
