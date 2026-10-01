"""A new game must reach the website's cache when it starts, not at its first move.

`start_match` writes `game_started` and publishes nothing, so every caller has to tell the web tier
itself (ADR-0046). None of them did: a tournament's next game was invisible on its event page —
however often it was reloaded — until the game's first move evicted the cache, which for a decision
model was minutes. These assert the call is made, with the event that invalidates the tournament
table, for each of the three ways a game is started.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.registry import sync_model_registry
from chessmark.db import tournaments as repo
from chessmark.db.enums import EventType
from chessmark.orchestration.tournament import advance
from chessmark.tournament import Format, TournamentConfig
from tests.api.conftest import as_user, fund
from tests.tournament.test_runner import make_tournament

pytestmark = pytest.mark.integration


def _record(monkeypatch: pytest.MonkeyPatch, module: str) -> list[tuple[uuid.UUID, list[str]]]:
    sent: list[tuple[uuid.UUID, list[str]]] = []

    async def fake(game_id: uuid.UUID, event_types: Iterable[str]) -> None:
        sent.append((uuid.UUID(str(game_id)), [str(t) for t in event_types]))

    # `raising=False` so that, before the fix, the tournament module (which imported nothing to
    # patch) fails on the assertion below rather than on the patch.
    monkeypatch.setattr(f"{module}.notify_web", fake, raising=False)
    return sent


def _started(sent: list[tuple[uuid.UUID, list[str]]]) -> set[uuid.UUID]:
    return {game_id for game_id, types in sent if str(EventType.GAME_STARTED) in types}


async def test_a_tournament_tells_the_site_about_each_game_it_starts(
    db: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _record(monkeypatch, "chessmark.orchestration.tournament")
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=2)
    )

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)

    assert step.started == 2
    in_flight = {row.game_id for row in await repo.in_flight(db, tournament_id)}
    assert len(in_flight) == 2
    assert _started(sent) == in_flight


async def test_creating_a_model_game_tells_the_site(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _record(monkeypatch, "chessmark.api.routes.games")
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "test/white", "display_name": "White Model"},
            {"openrouter_id": "test/black", "display_name": "Black Model"},
        ],
    )
    await db.commit()
    await fund(db, "user_started")

    response = await client.post(
        "/games",
        json={"white": "test/white", "black": "test/black", "max_plies": 20},
        headers=as_user("user_started"),
    )

    assert response.status_code == 201, response.text
    assert _started(sent) == {uuid.UUID(response.json()["id"])}


async def test_sitting_down_tells_the_site_even_when_the_person_moves_first(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the person on White nothing is enqueued, so no first move is coming from our side."""
    sent = _record(monkeypatch, "chessmark.api.routes.games")
    await sync_model_registry(
        db, [{"openrouter_id": "test/opponent", "display_name": "Opponent Model"}]
    )
    await db.commit()
    await fund(db)

    response = await client.post(
        "/games/human", json={"model": "test/opponent", "colour": "white"}, headers=as_user()
    )

    assert response.status_code == 201, response.text
    assert _started(sent) == {uuid.UUID(response.json()["id"])}
