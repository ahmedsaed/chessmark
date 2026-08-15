"""A game written to Postgres and read back must be the same game.

This is the phase's central claim: `docs/ARCHITECTURE.md` says the durable record is Postgres and
everything else is derived from it. If a stored game cannot be replayed to the position it
actually reached, the whole benchmark is unreproducible.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import Game, Ply
from chessmark.db.repositories import (
    GameNotFoundError,
    add_player,
    create_game,
    finish_game,
    get_game,
    list_games,
    load_moves_san,
    load_plies,
    record_ply,
)
from chessmark.game import ChessBoard, Colour, GameResult, Referee, Termination

# The Opera Game again — 33 plies of real chess including castling, captures, and mate.
OPERA_GAME = [
    "e4",
    "e5",
    "Nf3",
    "d6",
    "d4",
    "Bg4",
    "dxe5",
    "Bxf3",
    "Qxf3",
    "dxe5",
    "Bc4",
    "Nf6",
    "Qb3",
    "Qe7",
    "Nc3",
    "c6",
    "Bg5",
    "b5",
    "Nxb5",
    "cxb5",
    "Bxb5+",
    "Nbd7",
    "O-O-O",
    "Rd8",
    "Rxd7",
    "Rxd7",
    "Rd1",
    "Qe6",
    "Bxd7+",
    "Nxd7",
    "Qb8+",
    "Nxb8",
    "Rd8#",
]
STARTING_FEN = ChessBoard().fen


async def _persist_full_game(db: AsyncSession) -> tuple[Game, Referee]:
    """Play a real game through the referee, writing every ply as it happens."""
    game = await create_game(db, start_fen=STARTING_FEN, is_ranked=True, prompt_version="v1")
    await add_player(
        db,
        game_id=game.id,
        colour=Colour.WHITE,
        kind=PlayerKind.MODEL,
        display_name="gpt-oss-20b",
    )
    await add_player(
        db,
        game_id=game.id,
        colour=Colour.BLACK,
        kind=PlayerKind.MODEL,
        display_name="nemotron-3-nano-30b-a3b",
    )

    referee = Referee(start_fen=STARTING_FEN)
    for move_text in OPERA_GAME:
        colour = referee.side_to_move
        outcome = referee.play(move_text)
        await record_ply(db, game_id=game.id, colour=colour, move=outcome, think_ms=2400)

    assert referee.outcome is not None
    await finish_game(db, game_id=game.id, outcome=referee.outcome)
    await db.commit()

    return game, referee


@pytest.mark.integration
async def test_a_persisted_game_replays_to_the_same_position(db: AsyncSession) -> None:
    game, referee = await _persist_full_game(db)
    db.expunge_all()

    moves = await load_moves_san(db, game.id)
    assert len(moves) == 33

    replay = ChessBoard(STARTING_FEN)
    for move in moves:
        replay.push(move)

    assert replay.fen == referee.board.fen
    assert replay.is_checkmate()


@pytest.mark.integration
async def test_ply_details_survive_the_round_trip(db: AsyncSession) -> None:
    game, _ = await _persist_full_game(db)
    db.expunge_all()

    plies = await load_plies(db, game.id)
    assert [p.ply_number for p in plies] == list(range(1, 34))
    assert [p.colour for p in plies[:4]] == [
        Colour.WHITE,
        Colour.BLACK,
        Colour.WHITE,
        Colour.BLACK,
    ]

    by_san = {p.san: p for p in plies}
    assert by_san["O-O-O"].is_castling
    assert by_san["dxe5"].is_capture
    assert by_san["Bxb5+"].is_check
    assert by_san["Rd8#"].is_checkmate
    assert by_san["Rd8#"].think_ms == 2400

    # Each ply's stored fen_before must be the previous ply's fen_after.
    for previous, current in itertools.pairwise(plies):
        assert current.fen_before == previous.fen_after


@pytest.mark.integration
async def test_the_outcome_is_recorded_on_the_game(db: AsyncSession) -> None:
    game, _ = await _persist_full_game(db)
    db.expunge_all()

    reloaded = await get_game(db, game.id)
    assert reloaded.status is GameStatus.FINISHED
    assert reloaded.result is GameResult.WHITE_WINS
    assert reloaded.termination is Termination.CHECKMATE
    assert reloaded.winner_colour is Colour.WHITE
    assert reloaded.ply_count == 33
    assert reloaded.ended_at is not None


@pytest.mark.integration
async def test_benchmark_configuration_is_recorded(db: AsyncSession) -> None:
    """BENCH-04: a ranked result is worthless if we cannot say what it ran under."""
    game = await create_game(db, start_fen=STARTING_FEN, is_ranked=True, prompt_version="v3")
    await db.commit()
    db.expunge_all()

    reloaded = await get_game(db, game.id)
    assert reloaded.is_ranked is True
    assert reloaded.prompt_version == "v3"
    assert reloaded.max_illegal_retries == 5
    assert reloaded.max_plies == 300
    # GAME-09: rule flags present from the first migration, defaulting to automatic.
    assert reloaded.auto_threefold_draw is True
    assert reloaded.auto_fifty_move_draw is True


@pytest.mark.integration
async def test_evaluation_columns_exist_and_default_to_null(db: AsyncSession) -> None:
    """BENCH-08: Phase 14 must be additive, so the columns ship now, empty."""
    game, _ = await _persist_full_game(db)
    db.expunge_all()

    ply = (await load_plies(db, game.id))[0]
    assert ply.eval_before_cp is None
    assert ply.eval_after_cp is None
    assert ply.cp_loss is None
    assert ply.classification is None
    assert ply.analysed_at is None
    assert ply.engine_version is None


@pytest.mark.integration
async def test_players_are_recorded_with_stable_names(db: AsyncSession) -> None:
    game, _ = await _persist_full_game(db)
    db.expunge_all()

    reloaded = await get_game(db, game.id)
    await db.refresh(reloaded, ["players"])
    names = {p.colour: p.display_name for p in reloaded.players}

    assert names == {
        Colour.WHITE: "gpt-oss-20b",
        Colour.BLACK: "nemotron-3-nano-30b-a3b",
    }


@pytest.mark.integration
async def test_money_is_stored_exactly(db: AsyncSession) -> None:
    """Invariant 4: cost comes from real token counts, so storage must not round it."""
    awkward = Decimal("0.00000123")
    game = await create_game(db, start_fen=STARTING_FEN, max_usd=Decimal("1.00"))
    game.total_cost_usd = awkward
    await db.commit()
    db.expunge_all()

    reloaded = await get_game(db, game.id)
    assert reloaded.total_cost_usd == awkward
    assert isinstance(reloaded.total_cost_usd, Decimal)


@pytest.mark.integration
async def test_duplicate_ply_numbers_are_rejected(db: AsyncSession) -> None:
    """ADR-0007: a redelivered turn job must fail loudly, never corrupt the record."""
    game = await create_game(db, start_fen=STARTING_FEN)
    referee = Referee()
    outcome = referee.play("e4")
    await record_ply(db, game_id=game.id, colour=Colour.WHITE, move=outcome, think_ms=1)
    await db.commit()

    duplicate = Ply(
        game_id=game.id,
        ply_number=1,
        colour=Colour.WHITE,
        san="d4",
        uci="d2d4",
        fen_before=STARTING_FEN,
        fen_after=STARTING_FEN,
    )
    db.add(duplicate)

    with pytest.raises(Exception, match=r"uq_plies_game_id_ply_number|duplicate key"):
        await db.commit()


@pytest.mark.integration
async def test_missing_game_raises_a_typed_error(db: AsyncSession) -> None:
    import uuid

    missing = uuid.uuid4()
    with pytest.raises(GameNotFoundError) as caught:
        await get_game(db, missing)

    assert caught.value.game_id == missing


@pytest.mark.integration
async def test_listing_filters_by_status(db: AsyncSession) -> None:
    finished, _ = await _persist_full_game(db)
    await create_game(db, start_fen=STARTING_FEN)
    await db.commit()

    running = await list_games(db, status=GameStatus.PENDING)
    done = await list_games(db, status=GameStatus.FINISHED)

    assert [g.id for g in done] == [finished.id]
    assert len(running) == 1
    assert len(await list_games(db)) == 2
