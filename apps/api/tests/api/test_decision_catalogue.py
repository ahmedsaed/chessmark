"""Decision models on the public surface: the catalogue, a new game, the leaderboard (ADR-0049)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import DECISION_VERSION
from chessmark.api.routes.events import _visible_frame
from chessmark.db.enums import GameStatus, ModelRuntime
from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match
from tests.api.conftest import as_user, fund

pytestmark = pytest.mark.integration


async def _model(db: AsyncSession, slug: str, *, decision: bool) -> ModelRegistry:
    model = ModelRegistry(
        openrouter_id=slug,
        display_name=slug.split("/")[-1],
        provider=slug.split("/")[0],
        prompt_usd_per_token=Decimal("0.000000042"),
        completion_usd_per_token=Decimal(0),
        # A decision model declares no parameters at all, tools included.
        supports_tools=not decision,
        runtime=ModelRuntime.DECISION if decision else ModelRuntime.LLM,
        # As the catalogue refresh leaves a decision model that answered its check (ADR-0051).
        decisions_checked=DECISION_VERSION if decision else None,
        context_length=32_000 if decision else 200_000,
    )
    db.add(model)
    await db.flush()
    db.add(
        ModelEndpoint(
            model_id=model.id,
            provider_name=f"host-{slug.split('/')[-1]}",
            quantization="unknown" if decision else "fp8",
            supports_tools=not decision,
            context_length=model.context_length,
            uptime_1d=99.0,
        )
    )
    await db.flush()
    return model


async def test_the_catalogue_lists_a_decision_model_and_says_so(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "typesafe/jev-1.13", decision=True)
    await _model(db, "vendor/chat", decision=False)
    await db.commit()

    rows = {m["openrouter_id"]: m for m in (await client.get("/models")).json()}
    assert rows["typesafe/jev-1.13"]["runtime"] == "decision"
    assert rows["vendor/chat"]["runtime"] == "llm"
    # Its endpoint declares no tools and is still a contestant a game can pin.
    assert [c["provider"] for c in rows["typesafe/jev-1.13"]["contestants"]] == ["host-jev-1.13"]


async def test_a_game_can_be_started_against_a_decision_model(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "typesafe/jev-1.13", decision=True)
    await _model(db, "vendor/chat", decision=False)
    await db.commit()
    await fund(db, "user_decider")

    response = await client.post(
        "/games",
        json={"white": "vendor/chat", "black": "typesafe/jev-1.13", "max_plies": 20},
        headers=as_user("user_decider"),
    )
    assert response.status_code == 201, response.text


async def test_a_chat_model_without_tools_is_still_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The exemption is the runtime's, not a weakening of AGENT-01 for everyone."""
    chat = await _model(db, "vendor/blind", decision=False)
    chat.supports_tools = False
    await _model(db, "vendor/chat", decision=False)
    await db.commit()
    await fund(db, "user_blind")

    response = await client.post(
        "/games",
        json={"white": "vendor/blind", "black": "vendor/chat"},
        headers=as_user("user_blind"),
    )
    assert response.status_code == 400
    assert "tool calling" in response.json()["detail"]


async def _finished(db: AsyncSession, white: str, black: str, result: GameResult) -> None:
    match = await create_match(
        db,
        white=Seat(display_name=white, model=white),
        black=Seat(display_name=black, model=black),
        is_ranked=True,
    )
    game = match.game
    game.status = GameStatus.FINISHED
    game.result = result
    game.termination = Termination.CHECKMATE
    game.ply_count = 40
    await db.commit()


async def test_decision_models_share_the_leaderboard_and_are_marked(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "typesafe/jev-1.13", decision=True)
    await _model(db, "jaredpalmer/kev-4b", decision=True)
    await _model(db, "vendor/chat", decision=False)
    # One game between two decision models, and one against a chat model: both count.
    await _finished(db, "typesafe/jev-1.13", "jaredpalmer/kev-4b", GameResult.WHITE_WINS)
    await _finished(db, "vendor/chat", "typesafe/jev-1.13", GameResult.BLACK_WINS)

    body = (await client.get("/leaderboard")).json()
    assert body["games_counted"] == 2
    runtimes = {row["model_slug"]: row["runtime"] for row in body["rows"]}
    assert runtimes == {
        "typesafe/jev-1.13": "decision",
        "jaredpalmer/kev-4b": "decision",
        "vendor/chat": "llm",
    }


def test_a_decision_frame_is_dropped_from_a_person_mid_game() -> None:
    """Invariant 8 on the live channel: the frame carries the whole distribution."""
    frame = {"frame": "block", "kind": "decision", "choice": "e5", "probabilities": [["e5", 0.6]]}
    assert _visible_frame(frame, withhold=True) is None
    assert _visible_frame(frame, withhold=False) == frame


async def test_a_decision_model_that_has_not_answered_its_check_cannot_be_seated(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Named by hand, it still has to have passed (ADR-0051) — or the first move is a 400."""
    unchecked = await _model(db, "respan/span-01", decision=True)
    unchecked.decisions_checked = None
    await _model(db, "vendor/chat", decision=False)
    await db.commit()
    await fund(db, "user_unchecked")

    response = await client.post(
        "/games",
        json={"white": "vendor/chat", "black": "respan/span-01"},
        headers=as_user("user_unchecked"),
    )
    assert response.status_code == 400
    assert "not been checked" in response.json()["detail"]
