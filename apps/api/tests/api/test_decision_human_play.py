"""A person against a decision model, end to end over HTTP (ADR-0049).

The two things only this path can show: that invariant 8 covers a decision model's answer — its
ranking of every move is its plan — and that draws pass both ways between a person and a seat that
can only answer yes-or-no questions.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import DECISION_VERSION
from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import plays
from chessmark.agents.scripted_decisions import deciding
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import Game, TranscriptMessage
from chessmark.game import Termination
from tests.api.conftest import as_user, fund
from tests.orchestration.conftest import run_next

pytestmark = pytest.mark.integration

MODEL = "typesafe/jev-1.13"


async def _new_game(client: AsyncClient, db: AsyncSession) -> str:
    await sync_model_registry(
        db,
        [
            {
                "openrouter_id": MODEL,
                "display_name": "TypeSafe: Jev 1.13",
                "supports_tools": False,
                "runtime": ModelRuntime.DECISION,
            }
        ],
    )
    await db.commit()
    await fund(db)
    response = await client.post(
        "/games/human", json={"model": MODEL, "colour": "white"}, headers=as_user()
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _decided(events: list[dict]) -> list[dict]:
    return [event["payload"] for event in events if event["type"] == "decided"]


async def test_a_person_can_sit_down_against_a_decision_model(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _new_game(client, db)
    row = await db.scalar(sa.select(Game).where(Game.id == game_id))
    assert row is not None
    assert row.decision_version == DECISION_VERSION
    assert row.prompt_version is None

    detail = (await client.get(f"/games/{game_id}")).json()
    runtimes = {p["kind"]: p["runtime"] for p in detail["players"]}
    assert runtimes["model"] == "decision"
    assert detail["decision_version"] == DECISION_VERSION


async def test_the_models_answer_is_withheld_while_the_person_is_playing(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """Invariant 8. What it *did* is public — the move is on the board — and what it weighed is
    not, until the game is over."""
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    await run_next(make_worker(plays([]), decide_fn=deciding(moves=["e5"])), queue)

    (live,) = _decided((await client.get(f"/games/{game_id}/events")).json())
    assert live["choice"] == "e5"
    assert live["action"] == "move"
    for withheld in ("probabilities", "confidence", "answers"):
        assert withheld not in live

    turns = (await client.get(f"/games/{game_id}/turns")).json()
    model_turn = next(t for t in turns if t.get("llm_call_count"))
    # The verbatim response carries the same answers, and is refused outright while live.
    raw = await client.get(f"/games/{game_id}/turns/{model_turn['id']}/raw")
    assert raw.status_code == 409

    await client.post(f"/games/{game_id}/resign", headers=as_user())
    (after,) = _decided((await client.get(f"/games/{game_id}/events")).json())
    assert after["probabilities"][0][0] == "e5"
    assert set(after["answers"]) == {"resign", "offer_draw"}


async def test_a_decision_models_offer_can_be_accepted_by_the_person(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    await run_next(
        make_worker(plays([]), decide_fn=deciding(moves=["e5"], answers={"offer_draw": 0.8})),
        queue,
    )

    response = await client.post(
        f"/games/{game_id}/draw/respond", json={"accept": True}, headers=as_user()
    )
    assert response.status_code == 200, response.text
    db.expunge_all()
    row = await db.scalar(sa.select(Game).where(Game.id == game_id))
    assert row is not None and row.termination is Termination.AGREED_DRAW


async def test_a_persons_offer_is_put_to_the_decision_model(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/draw", headers=as_user())
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    decide = deciding(answers={"accept_draw": 0.7})
    worker = make_worker(plays([]), decide_fn=decide)
    # The offer settles with a job of its own, which finds a person to move and stops; the move's
    # job is the one that reaches the model.
    while await run_next(worker, queue) is not None:
        pass

    assert "accept_draw" in decide.calls[0]["questions"]  # type: ignore[attr-defined]
    # The offer reached it as a question, and not as prose into a transcript it does not have.
    written = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TranscriptMessage)
        .where(TranscriptMessage.game_id == game_id)
    )
    assert written == 0
    db.expunge_all()
    row = await db.scalar(sa.select(Game).where(Game.id == game_id))
    assert row is not None and row.termination is Termination.AGREED_DRAW
