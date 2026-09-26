"""Decision models playing real games through the real worker (ADR-0049).

Only the network is scripted (`agents/scripted_decisions.py`). Match creation, the worker's fork,
the request, the parsing, the referee, the record and every event are the production path.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import (
    CLAIM_DRAW_QUESTION,
    DECISION_VERSION,
    OFFER_DRAW_QUESTION,
)
from chessmark.agents.decisions import DecisionHttpError
from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.agents.scripted import plays, step, tool_call
from chessmark.agents.scripted_decisions import deciding
from chessmark.agents.tools import TOOL_SCHEMA_VERSION
from chessmark.core.cooldown import ProviderCooldown
from chessmark.db.enums import EventType, GameStatus, ModelRuntime, TurnStatus
from chessmark.db.models import (
    Game,
    GameEvent,
    LlmCall,
    Message,
    ModelEndpoint,
    ModelRegistry,
    Player,
    TranscriptMessage,
    Turn,
)
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match, start_match
from chessmark.orchestration.queue import TurnQueue
from chessmark.orchestration.worker import PAUSED, TURN_FAILED
from tests.support import run_next

pytestmark = pytest.mark.integration

JEV = "typesafe/jev-1.13"
KEV = "jaredpalmer/kev-4b"
CHAT = "scripted/chat"


async def _register(db: AsyncSession) -> None:
    """The two decision models as the catalogue sync writes them, with the endpoint each pins."""
    for slug, provider in ((JEV, "TypeSafe"), (KEV, "SiliconFlow")):
        row = ModelRegistry(
            openrouter_id=slug,
            display_name=slug,
            provider=slug.split("/")[0],
            context_length=32_000 if slug == JEV else 8_192,
            supports_tools=False,
            runtime=ModelRuntime.DECISION,
        )
        db.add(row)
        await db.flush()
        db.add(
            ModelEndpoint(
                model_id=row.id,
                provider_name=provider,
                context_length=row.context_length,
                supports_tools=False,
                uptime_1d=100.0,
            )
        )
    await db.flush()


async def _start(
    db: AsyncSession,
    queue: TurnQueue,
    *,
    white: str = JEV,
    black: str = KEV,
    **kwargs: Any,
) -> Game:
    await _register(db)
    match = await create_match(
        db,
        white=Seat(display_name=white, model=white),
        black=Seat(display_name=black, model=black),
        **kwargs,
    )
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)
    return match.game


async def _play(worker: Any, queue: TurnQueue, *, turns: int = 40) -> list[Any]:
    handled = []
    for _ in range(turns):
        result = await run_next(worker, queue)
        if result is None:
            break
        handled.append(result)
    return handled


async def _events(db: AsyncSession, game_id: Any, kind: EventType) -> list[GameEvent]:
    db.expunge_all()
    return list(
        await db.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.type == kind)
            .order_by(GameEvent.seq)
        )
    )


async def _game(db: AsyncSession, game_id: Any) -> Game:
    db.expunge_all()
    game = await db.get(Game, game_id)
    assert game is not None
    return game


FOOLS_MATE = ["f3", "e5", "g4", "Qh4"]


class TestMatchCreation:
    async def test_each_seat_records_the_harness_it_runs(
        self, db: AsyncSession, queue: Any
    ) -> None:
        game = await _start(db, queue)
        seats = list(await db.scalars(sa.select(Player).where(Player.game_id == game.id)))
        assert {p.runtime for p in seats} == {ModelRuntime.DECISION}
        assert all(p.system_prompt_version is None for p in seats)

    async def test_a_game_of_decision_models_carries_only_the_decision_version(
        self, db: AsyncSession, queue: Any
    ) -> None:
        """A chat prompt it never ran must not be able to retire it (`bench/ratable.judge`)."""
        game = await _start(db, queue)
        assert game.decision_version == DECISION_VERSION
        assert (game.prompt_version, game.tool_schema_version) == (None, None)

    async def test_a_mixed_game_carries_both(self, db: AsyncSession, queue: Any) -> None:
        game = await _start(db, queue, white=CHAT)
        assert game.decision_version == DECISION_VERSION
        assert (game.prompt_version, game.tool_schema_version) == (
            PROMPT_VERSION,
            TOOL_SCHEMA_VERSION,
        )

    async def test_a_decision_seat_has_no_transcript(self, db: AsyncSession, queue: Any) -> None:
        game = await _start(db, queue, white=CHAT)
        rows = await db.scalars(
            sa.select(TranscriptMessage.player_id).where(TranscriptMessage.game_id == game.id)
        )
        seated = {
            p.id: p.runtime
            for p in await db.scalars(sa.select(Player).where(Player.game_id == game.id))
        }
        assert {seated[player_id] for player_id in rows} == {ModelRuntime.LLM}

    async def test_the_seat_is_pinned_to_its_endpoint(self, db: AsyncSession, queue: Any) -> None:
        game = await _start(db, queue)
        seats = {
            p.display_name: p
            for p in await db.scalars(sa.select(Player).where(Player.game_id == game.id))
        }
        assert seats[JEV].provider_routing["only"] == ["TypeSafe"]
        assert seats[KEV].provider_routing["only"] == ["SiliconFlow"]


class TestATurn:
    async def test_one_call_one_record_one_move(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        decide = deciding(moves=["e4"], cost=0.00005)
        game = await _start(db, queue)
        await run_next(make_worker(plays([]), decide_fn=decide), queue)

        calls = list(await db.scalars(sa.select(LlmCall).where(LlmCall.game_id == game.id)))
        assert len(calls) == 1
        assert calls[0].model_slug == JEV
        assert calls[0].provider == "Scripted"
        assert calls[0].response["model"].startswith(JEV)
        # The request went out pinned, exactly as recorded.
        assert decide.calls[0]["provider"]["only"] == ["TypeSafe"]  # type: ignore[attr-defined]

        moves = await _events(db, game.id, EventType.MOVE_MADE)
        assert [m.payload["san"] for m in moves] == ["e4"]

        turn = (await db.scalars(sa.select(Turn).where(Turn.game_id == game.id))).one()
        assert (turn.status, turn.llm_call_count, turn.ply_number) == (TurnStatus.COMPLETED, 1, 1)
        assert str(turn.cost_usd) == "0.00005000"

    async def test_the_answer_is_an_event_with_the_whole_distribution(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        game = await _start(db, queue)
        await run_next(make_worker(plays([]), decide_fn=deciding(moves=["d4"])), queue)

        (decided,) = await _events(db, game.id, EventType.DECIDED)
        payload = decided.payload
        assert (payload["action"], payload["choice"], payload["options"]) == ("move", "d4", 20)
        assert payload["probabilities"][0][0] == "d4"
        assert len(payload["probabilities"]) == 20
        assert set(payload["answers"]) == {"resign", "offer_draw"}
        assert payload["offers_draw"] is False

    async def test_the_spend_reaches_the_seat_and_the_game(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        game = await _start(db, queue)
        await _play(
            make_worker(plays([]), decide_fn=deciding(moves=["e4", "e5"], cost=0.001)),
            queue,
            turns=2,
        )
        reloaded = await _game(db, game.id)
        assert str(reloaded.total_cost_usd) == "0.00200000"
        seats = list(await db.scalars(sa.select(Player).where(Player.game_id == game.id)))
        assert sorted(str(p.total_cost_usd) for p in seats) == ["0.00100000", "0.00100000"]


class TestEndings:
    async def test_a_scripted_game_plays_through_to_checkmate(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        game = await _start(db, queue)
        await _play(make_worker(plays([]), decide_fn=deciding(moves=FOOLS_MATE)), queue)

        reloaded = await _game(db, game.id)
        assert reloaded.status is GameStatus.FINISHED
        assert reloaded.termination is Termination.CHECKMATE
        assert reloaded.result is GameResult.BLACK_WINS
        assert reloaded.ply_count == 4

    async def test_a_seat_that_answers_yes_to_resigning_resigns(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        game = await _start(db, queue)
        await _play(make_worker(plays([]), decide_fn=deciding(answers={"resign": 0.9})), queue)

        reloaded = await _game(db, game.id)
        assert reloaded.termination is Termination.RESIGNATION
        # White was asked first, and a resigned player does not also move.
        assert reloaded.result is GameResult.BLACK_WINS
        assert reloaded.ply_count == 0
        (decided,) = await _events(db, game.id, EventType.DECIDED)
        assert decided.payload["action"] == "resign"

    async def test_just_under_the_gate_plays_on(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        """The gate is the boundary, not a vibe: 0.49 moves, and the game goes on."""
        game = await _start(db, queue)
        await run_next(make_worker(plays([]), decide_fn=deciding(answers={"resign": 0.49})), queue)
        reloaded = await _game(db, game.id)
        assert (reloaded.status, reloaded.ply_count) == (GameStatus.RUNNING, 1)

    async def test_an_offer_rides_with_the_move_and_the_other_seat_can_take_it(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        game = await _start(db, queue)
        decide = deciding(answers={"offer_draw": 0.9, "accept_draw": 0.9})
        await _play(make_worker(plays([]), decide_fn=decide), queue)

        offers = await _events(db, game.id, EventType.DRAW_OFFERED)
        assert len(offers) == 1 and offers[0].payload["ply"] == 1

        reloaded = await _game(db, game.id)
        assert reloaded.termination is Termination.AGREED_DRAW
        assert reloaded.ply_count == 1
        # Black was asked to accept, and not also to offer — that would be answering itself.
        black_request = decide.calls[1]  # type: ignore[attr-defined]
        assert OFFER_DRAW_QUESTION not in black_request["questions"]

    async def test_a_declined_offer_is_not_repeated_until_the_position_changes(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        """Once declined, not again until a capture or a pawn move (ADR-0049). The first long real
        game had a seat offering beside thirty of its thirty-six moves."""
        # White offers with Nf3; Black moves on, which declines it. Knights shuffle — nothing
        # irreversible — so White is not asked again. Then e4, a pawn move, and it may.
        moves = ["Nf3", "Nf6", "Ng1", "Ng8", "e4", "e5", "Nf3"]
        # White alone offers: Black's own offer, left open, would ask White to accept instead.
        decide = deciding(moves=moves, by_side={"white": {"offer_draw": 0.9}})
        game = await _start(db, queue)
        await _play(make_worker(plays([]), decide_fn=decide), queue, turns=7)

        asked = {
            index + 1: OFFER_DRAW_QUESTION in call["questions"]
            for index, call in enumerate(decide.calls)  # type: ignore[attr-defined]
            if index % 2 == 0  # White's turns
        }
        # Ply 1 offers; plies 3 and 5 follow only knight moves since it; ply 7 follows e4 and e5.
        assert asked == {1: True, 3: False, 5: False, 7: True}
        offers = await _events(db, game.id, EventType.DRAW_OFFERED)
        white = [o for o in offers if o.payload["colour"] == "white"]
        assert [o.payload["ply"] for o in white] == [1, 7]

    async def test_a_threefold_claim_is_asked_only_once_it_is_open_and_then_taken(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"] * 2
        decide = deciding(moves=shuffle, answers={"claim_draw": 0.9})
        game = await _start(db, queue)
        await _play(make_worker(plays([]), decide_fn=decide), queue)

        reloaded = await _game(db, game.id)
        assert reloaded.termination is Termination.THREEFOLD_REPETITION
        assert reloaded.ply_count == 8
        asked = [CLAIM_DRAW_QUESTION in c["questions"] for c in decide.calls]  # type: ignore[attr-defined]
        assert asked == [False] * 8 + [True]

    async def test_a_seat_that_declines_the_claim_plays_on(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"] * 2 + ["e4"]
        game = await _start(db, queue)
        await _play(
            make_worker(plays([]), decide_fn=deciding(moves=shuffle, answers={"claim_draw": 0.1})),
            queue,
            turns=9,
        )
        reloaded = await _game(db, game.id)
        assert (reloaded.status, reloaded.ply_count) == (GameStatus.RUNNING, 9)


class TestAgainstAChatModel:
    async def test_a_chat_models_offer_reaches_a_decision_seat_that_accepts_it(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        async def offers(**_: Any) -> Any:
            return step(tool_call("offer_draw"), tool_call("make_move", move="e4"))

        game = await _start(db, queue, white=CHAT)
        worker = make_worker(
            _once_then_stop(offers), decide_fn=deciding(answers={"accept_draw": 0.9})
        )
        await _play(worker, queue)
        reloaded = await _game(db, game.id)
        assert reloaded.termination is Termination.AGREED_DRAW

    async def test_talk_is_recorded_but_not_delivered_to_a_seat_that_cannot_read(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        async def talks(**_: Any) -> Any:
            return step(tool_call("say", message="good luck"), tool_call("make_move", move="e4"))

        game = await _start(db, queue, white=CHAT)
        await run_next(make_worker(_once_then_stop(talks)), queue)

        said = list(await db.scalars(sa.select(Message).where(Message.game_id == game.id)))
        assert [m.content for m in said] == ["good luck"]
        black = (
            await db.scalars(
                sa.select(Player).where(Player.game_id == game.id, Player.colour == "black")
            )
        ).one()
        transcript = await db.scalars(
            sa.select(TranscriptMessage).where(TranscriptMessage.player_id == black.id)
        )
        assert list(transcript) == []


def _once_then_stop(first: Any) -> Any:
    """A chat seat that does `first` on its first call and stops on every call after."""
    from chessmark.agents.scripted import STOPS

    state = {"called": False}

    async def _complete(**kwargs: Any) -> Any:
        if state["called"]:
            return STOPS
        state["called"] = True
        return await first(**kwargs)

    return _complete


class TestFailures:
    async def test_an_unusable_answer_fails_the_turn_and_forfeits_nobody(
        self, db: AsyncSession, queue: Any, make_worker: Any
    ) -> None:
        """Invariant 11: the endpoint returned nonsense; the model chose nothing."""
        game = await _start(db, queue)
        handled = await run_next(make_worker(plays([]), decide_fn=deciding(malformed=True)), queue)

        assert handled.outcome == TURN_FAILED
        reloaded = await _game(db, game.id)
        assert (reloaded.status, reloaded.ply_count) == (GameStatus.RUNNING, 0)
        assert reloaded.termination is None
        seats = list(await db.scalars(sa.select(Player).where(Player.game_id == game.id)))
        assert not any(p.forfeited for p in seats)

    async def test_a_rate_limit_pauses_the_game(
        self, db: AsyncSession, queue: Any, make_worker: Any, redis: Any
    ) -> None:
        async def limited(_: dict[str, Any]) -> dict[str, Any]:
            response = httpx.Response(429, json={"error": {"message": "TPM limit", "code": 429}})
            raise DecisionHttpError(429, response.text, response)

        game = await _start(db, queue)
        handled = await run_next(
            make_worker(plays([]), decide_fn=limited, cooldown=ProviderCooldown(redis)), queue
        )
        assert handled.outcome == PAUSED
        reloaded = await _game(db, game.id)
        assert reloaded.status is GameStatus.PAUSED
        assert reloaded.termination is None
