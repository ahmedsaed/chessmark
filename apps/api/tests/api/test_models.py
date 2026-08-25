"""The model registry and one model's page (Phase 20, UI-07, BENCH-02 extended).

The load-bearing test here is the reconciliation one. A model page prints numbers the leaderboard
also prints, from a different query over the same data — and two views of one figure that are
computed separately is exactly how a project ends up publishing a cost its own call log disagrees
with. This asserts they agree by construction.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import Game, LlmCall, ModelRegistry, Player, Turn
from chessmark.db.stats import model_stats
from chessmark.game import GameResult, Termination

pytestmark = pytest.mark.integration

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


async def _model(db: AsyncSession, slug: str) -> ModelRegistry:
    await sync_model_registry(db, [{"openrouter_id": slug, "display_name": slug}])
    await db.flush()
    row = await db.scalar(sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug))
    assert row is not None
    return row


async def _finished_game(
    db: AsyncSession,
    white: ModelRegistry,
    black: ModelRegistry,
    *,
    result: GameResult,
    plies: int = 10,
    illegal_white: int = 0,
) -> Game:
    game = Game(
        status=GameStatus.FINISHED,
        result=result,
        termination=Termination.CHECKMATE,
        ply_count=plies,
        start_fen=START,
    )
    db.add(game)
    await db.flush()

    for colour, model, illegal in (("white", white, illegal_white), ("black", black, 0)):
        db.add(
            Player(
                game_id=game.id,
                colour=colour,
                kind=PlayerKind.MODEL,
                model_id=model.id,
                display_name=model.display_name,
                illegal_attempts=illegal,
                sampling={"model": model.openrouter_id},
            )
        )
    await db.flush()
    return game


async def _call(
    db: AsyncSession,
    game: Game,
    slug: str,
    *,
    prompt: int,
    completion: int,
    cached: int,
    cost: str,
    latency: int,
) -> None:
    # `turns.player_id` is not nullable — a call always belongs to a seat.
    player = await db.scalar(
        sa.select(Player)
        .join(ModelRegistry, ModelRegistry.id == Player.model_id)
        .where(Player.game_id == game.id, ModelRegistry.openrouter_id == slug)
        .limit(1)
    )
    assert player is not None, f"no seat for {slug} in this game"

    turn = Turn(game_id=game.id, player_id=player.id, ply_number=1)
    db.add(turn)
    await db.flush()
    db.add(
        LlmCall(
            game_id=game.id,
            turn_id=turn.id,
            sequence=1,
            model_slug=slug,
            request={},
            response={},
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
            cost_usd=Decimal(cost),
            latency_ms=latency,
        )
    )
    await db.flush()


# ====================================================================== the reconciliation


async def test_aggregates_reconcile_exactly_with_the_call_log(db: AsyncSession) -> None:
    """The exit criterion. A page must not print a cost `llm_calls` disagrees with."""
    model = await _model(db, "vendor/reconcile")
    other = await _model(db, "vendor/other")
    game = await _finished_game(db, model, other, result=GameResult.WHITE_WINS)

    await _call(
        db,
        game,
        "vendor/reconcile",
        prompt=1000,
        completion=50,
        cached=400,
        cost="0.001",
        latency=100,
    )
    await _call(
        db,
        game,
        "vendor/reconcile",
        prompt=2000,
        completion=70,
        cached=600,
        cost="0.002",
        latency=300,
    )
    # A call by the opponent must not leak into this model's totals.
    await _call(
        db, game, "vendor/other", prompt=9999, completion=999, cached=0, cost="9.99", latency=9999
    )
    await db.commit()

    stats = await model_stats(db, model)

    truth = (
        await db.execute(
            sa.select(
                sa.func.count(LlmCall.id),
                sa.func.sum(LlmCall.prompt_tokens),
                sa.func.sum(LlmCall.completion_tokens),
                sa.func.sum(LlmCall.cached_tokens),
                sa.func.sum(LlmCall.cost_usd),
                sa.func.avg(LlmCall.latency_ms),
            ).where(LlmCall.model_slug == "vendor/reconcile")
        )
    ).one()

    assert stats.llm_calls == truth[0]
    assert stats.prompt_tokens == truth[1]
    assert stats.completion_tokens == truth[2]
    assert stats.cached_tokens == truth[3]
    assert stats.total_cost_usd == truth[4]
    assert stats.mean_latency_ms == pytest.approx(float(truth[5]))


async def test_the_cache_rate_divides_by_the_prompt_only(db: AsyncSession) -> None:
    """Only prompt tokens can be cached; dividing by the total would under-report the real rate."""
    model = await _model(db, "vendor/cache")
    other = await _model(db, "vendor/opponent")
    game = await _finished_game(db, model, other, result=GameResult.DRAW)
    await _call(
        db, game, "vendor/cache", prompt=1000, completion=500, cached=800, cost="0.01", latency=10
    )
    await db.commit()

    stats = await model_stats(db, model)

    assert stats.cache_rate == pytest.approx(0.8)


async def test_a_model_that_has_never_played_has_no_cache_rate(db: AsyncSession) -> None:
    """`None`, not zero — a model with no calls has not achieved 0%, it has not been measured."""
    model = await _model(db, "vendor/unplayed")
    await db.commit()

    stats = await model_stats(db, model)

    assert stats.games == 0
    assert stats.cache_rate is None
    assert stats.cost_per_game == Decimal(0)
    assert stats.illegal_per_move == 0.0


# ====================================================================== results


async def test_wins_and_losses_are_counted_from_the_seat(db: AsyncSession) -> None:
    model = await _model(db, "vendor/winner")
    other = await _model(db, "vendor/loser")

    await _finished_game(db, model, other, result=GameResult.WHITE_WINS)
    await _finished_game(db, other, model, result=GameResult.WHITE_WINS)
    await _finished_game(db, model, other, result=GameResult.DRAW)
    await db.commit()

    stats = await model_stats(db, model)

    assert (stats.wins, stats.draws, stats.losses) == (1, 1, 1)
    assert stats.games == 3


async def test_a_model_playing_itself_counts_one_game_and_two_seats(db: AsyncSession) -> None:
    """It won one and lost one. Counting the game twice would inflate every per-game average."""
    model = await _model(db, "vendor/narcissus")

    await _finished_game(db, model, model, result=GameResult.WHITE_WINS)
    await db.commit()

    stats = await model_stats(db, model)

    assert stats.games == 1
    assert stats.seats == 2
    assert (stats.wins, stats.losses) == (1, 1)


async def test_a_running_game_has_no_result_to_count(db: AsyncSession) -> None:
    """A game still being played must not move a number that is still being decided."""
    model = await _model(db, "vendor/inflight")
    other = await _model(db, "vendor/rival")
    game = await _finished_game(db, model, other, result=GameResult.WHITE_WINS)
    game.status = GameStatus.RUNNING
    game.result = GameResult.ONGOING
    await db.commit()

    stats = await model_stats(db, model)

    assert stats.games == 1  # it is still a game it has appeared in
    assert (stats.wins, stats.draws, stats.losses) == (0, 0, 0)


async def test_illegal_rate_is_per_move_not_per_game(db: AsyncSession) -> None:
    """The benchmark's headline number, and the denominator is this seat's own moves."""
    model = await _model(db, "vendor/sloppy")
    other = await _model(db, "vendor/clean")
    await _finished_game(db, model, other, result=GameResult.DRAW, plies=10, illegal_white=2)
    await db.commit()

    stats = await model_stats(db, model)

    assert stats.moves_played == 5  # white plays half of ten plies
    assert stats.illegal_attempts == 2
    assert stats.illegal_per_move == pytest.approx(0.4)


# ====================================================================== the endpoints


async def test_a_model_page_is_public(client: AsyncClient, db: AsyncSession) -> None:
    """Reading is open to everyone (AUTH-02)."""
    await _model(db, "vendor/public")
    await db.commit()

    assert (await client.get("/models/vendor/public")).status_code == 200


async def test_an_unknown_slug_is_404_not_500(client: AsyncClient) -> None:
    response = await client.get("/models/nobody/nothing")

    assert response.status_code == 404
    assert "registry" in response.text.lower()


async def test_a_slug_with_a_slash_resolves(client: AsyncClient, db: AsyncSession) -> None:
    """An OpenRouter id contains a slash. Without a path converter this is a 404 by routing."""
    await _model(db, "vendor/with-slash")
    await db.commit()

    body = (await client.get("/models/vendor/with-slash")).json()

    assert body["openrouter_id"] == "vendor/with-slash"


async def test_a_model_with_no_games_renders_an_empty_state(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "vendor/fresh")
    await db.commit()

    body = (await client.get("/models/vendor/fresh")).json()

    assert body["stats"]["games"] == 0
    assert body["ratings"] == []


async def test_games_can_be_filtered_by_model(client: AsyncClient, db: AsyncSession) -> None:
    wanted = await _model(db, "vendor/wanted")
    unwanted = await _model(db, "vendor/unwanted")
    mine = await _finished_game(db, wanted, unwanted, result=GameResult.DRAW)
    await _finished_game(db, unwanted, unwanted, result=GameResult.DRAW)
    await db.commit()

    body = (await client.get("/games", params={"model": "vendor/wanted"})).json()

    assert [row["id"] for row in body] == [str(mine.id)]


async def test_filtering_by_a_model_that_played_itself_returns_one_game(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A join would return the game once per seat."""
    model = await _model(db, "vendor/twice")
    game = await _finished_game(db, model, model, result=GameResult.DRAW)
    await db.commit()

    body = (await client.get("/games", params={"model": "vendor/twice"})).json()

    assert [row["id"] for row in body] == [str(game.id)]


async def test_filtering_by_an_unknown_model_is_empty_not_an_error(client: AsyncClient) -> None:
    response = await client.get("/games", params={"model": "vendor/never"})

    assert response.status_code == 200
    assert response.json() == []
