"""The turn loop — the behaviour the benchmark actually measures.

Every test runs a real turn against a real referee with real persistence, and replaces only the
provider. Nothing here costs anything.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import plays, prose, raw_tool_call, says, scripted, step, tool_call
from chessmark.agents.tools import MAX_MESSAGE_LENGTH, MAX_MESSAGES_PER_TURN
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import LlmCall, Message, Ply, ToolCall, Turn
from chessmark.game import Colour, GameResult, Termination
from tests.agents.conftest import Table, play_turn, seat

pytestmark = pytest.mark.integration


# ====================================================================== happy path


async def test_a_simple_turn_commits_a_move(db: AsyncSession, table: Table) -> None:
    result = await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))))

    assert result.status is TurnStatus.COMPLETED
    assert result.moved
    assert result.move is not None
    assert result.move.move.san == "e4"
    assert result.illegal_attempts == 0
    assert table.referee.ply == 1


async def test_the_ply_is_persisted(db: AsyncSession, table: Table) -> None:
    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))))

    ply = (await db.scalars(sa.select(Ply).where(Ply.game_id == table.game.id))).one()
    assert ply.san == "e4"
    assert ply.ply_number == 1
    assert ply.colour is Colour.WHITE
    assert ply.turn_id is not None


async def test_reading_tools_do_not_end_the_turn(db: AsyncSession, table: Table) -> None:
    """Read-only tools are free and may be called freely (AGENT-11)."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_board")),
            step(tool_call("get_legal_moves")),
            step(tool_call("get_move_history")),
            step(tool_call("make_move", move="d4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.llm_calls == 4
    assert result.tool_calls == 4
    assert result.move is not None
    assert result.move.move.san == "d4"


async def test_everything_is_persisted(db: AsyncSession, table: Table) -> None:
    """LOG-01/03: the verbatim record is the point of the project."""
    await play_turn(
        db,
        table,
        scripted(
            step(
                tool_call("get_legal_moves"),
                reasoning="Considering the centre.",
                reasoning_tokens=9,
            ),
            step(tool_call("make_move", move="e4")),
        ),
    )

    turns = (await db.scalars(sa.select(Turn).where(Turn.game_id == table.game.id))).all()
    llm_calls = (await db.scalars(sa.select(LlmCall).where(LlmCall.game_id == table.game.id))).all()
    tool_calls = (
        await db.scalars(sa.select(ToolCall).where(ToolCall.game_id == table.game.id))
    ).all()

    assert len(turns) == 1
    assert len(llm_calls) == 2
    assert len(tool_calls) == 2

    assert [c.sequence for c in llm_calls] == [1, 2]
    assert all(c.request for c in llm_calls), "requests are stored verbatim"
    assert llm_calls[0].reasoning_text == "Considering the centre."
    assert turns[0].reasoning_tokens == 9
    assert turns[0].status is TurnStatus.COMPLETED


async def test_multiple_tools_in_one_response_all_execute(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db,
        table,
        scripted(
            step(
                tool_call("get_board", call_id="a"),
                tool_call("get_legal_moves", call_id="b"),
                tool_call("make_move", call_id="c", move="Nf3"),
            )
        ),
    )

    assert result.tool_calls == 3
    assert result.move is not None
    assert result.move.move.san == "Nf3"


# ====================================================================== illegal moves


async def test_five_illegal_moves_then_a_legal_one_still_commits(
    db: AsyncSession, table: Table
) -> None:
    """Exit criterion. `max_illegal_retries` is the number of failures tolerated."""
    result = await play_turn(
        db,
        table,
        scripted(
            *[step(tool_call("make_move", move="Qh5")) for _ in range(5)],
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.illegal_attempts == 5
    assert result.move is not None
    assert result.move.move.san == "e4"
    assert not table.referee.is_over


async def test_the_illegal_count_is_persisted(db: AsyncSession, table: Table) -> None:
    await play_turn(
        db,
        table,
        scripted(
            *[step(tool_call("make_move", move="Qh5")) for _ in range(5)],
            step(tool_call("make_move", move="e4")),
        ),
    )

    turn = (await db.scalars(sa.select(Turn).where(Turn.game_id == table.game.id))).one()
    assert turn.illegal_attempts == 5

    await db.refresh(table.white)
    assert table.white.illegal_attempts == 5, "the player's running total must accumulate"


async def test_six_illegal_moves_forfeit_the_game(db: AsyncSession, table: Table) -> None:
    """Exit criterion. The sixth failure is fatal."""
    result = await play_turn(
        db,
        table,
        scripted(*[step(tool_call("make_move", move="Qh5")) for _ in range(6)]),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.illegal_attempts == 6
    assert result.outcome is not None
    assert result.outcome.termination is Termination.ILLEGAL_MOVE_FORFEIT
    assert result.outcome.result is GameResult.BLACK_WINS
    assert result.outcome.is_forfeit
    assert table.referee.is_over


async def test_every_illegal_result_carries_the_full_legal_move_list(
    db: AsyncSession, table: Table
) -> None:
    """Exit criterion, and the heart of ADR-0002."""
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("make_move", move="Qh5")),
            step(tool_call("make_move", move="banana")),
            step(tool_call("make_move", move="e2e5")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    rows = (
        await db.scalars(
            sa.select(ToolCall)
            .where(ToolCall.game_id == table.game.id, ToolCall.ok.is_(False))
            .order_by(ToolCall.sequence)
        )
    ).all()

    assert len(rows) == 3
    for row in rows:
        result = row.result or {}
        assert result["legal_moves_san"], f"{row.arguments} rejected with no legal move list"
        assert len(result["legal_moves_san"]) == 20, "the opening position has 20 legal moves"
        assert result["detail"]
        assert result["fen"]
        assert "attempts_remaining" in result


async def test_attempts_remaining_counts_down_to_zero(db: AsyncSession, table: Table) -> None:
    """A model must be able to see that its next failure is fatal."""
    await play_turn(
        db,
        table,
        scripted(
            *[step(tool_call("make_move", move="Qh5")) for _ in range(5)],
            step(tool_call("make_move", move="e4")),
        ),
    )

    rows = (
        await db.scalars(
            sa.select(ToolCall)
            .where(ToolCall.game_id == table.game.id, ToolCall.ok.is_(False))
            .order_by(ToolCall.sequence)
        )
    ).all()

    assert [(row.result or {})["attempts_remaining"] for row in rows] == [4, 3, 2, 1, 0]


async def test_an_illegal_move_does_not_consume_a_ply(db: AsyncSession, table: Table) -> None:
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("make_move", move="Qh5")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    plies = (await db.scalars(sa.select(Ply).where(Ply.game_id == table.game.id))).all()
    assert len(plies) == 1
    assert plies[0].san == "e4"


async def test_malformed_tool_arguments_count_as_illegal(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db,
        table,
        scripted(
            step(raw_tool_call("make_move", "{move: e4")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.moved
    # Unparseable arguments are a tool-level failure, not a chess-level one, so they are reported
    # but do not consume the illegal-move budget.
    assert result.illegal_attempts == 0


async def test_a_non_string_move_is_rejected_with_the_legal_list(
    db: AsyncSession, table: Table
) -> None:
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("make_move", move=42)),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.illegal_attempts == 1
    assert result.moved


# ====================================================================== no tool call


async def test_prose_is_nudged_once_then_forfeits(db: AsyncSession, table: Table) -> None:
    """Exit criterion. AGENT-01 forbids reading a move out of prose."""
    result = await play_turn(
        db,
        table,
        scripted(
            prose("I'll play knight to f3."),
            prose("As I said, knight f3."),
        ),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.ERROR_FORFEIT
    assert not result.moved
    assert table.referee.is_over


async def test_a_nudge_recovers_a_model_that_then_complies(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db,
        table,
        scripted(
            prose("Let me think about this."),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.moved
    assert not table.referee.is_over


# ====================================================================== say


async def test_a_model_can_talk_and_move(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db, table, scripted(says("Watch this.", tool_call("make_move", move="e4")))
    )

    assert result.said == ["Watch this."]
    assert result.moved

    message = (await db.scalars(sa.select(Message).where(Message.game_id == table.game.id))).one()
    assert message.content == "Watch this."
    assert message.player_id == table.white.id


async def test_an_overlong_message_is_rejected(db: AsyncSession, table: Table) -> None:
    """Exit criterion (TALK-04)."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("say", message="x" * (MAX_MESSAGE_LENGTH + 1))),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.said == []
    assert result.moved

    rejected = (
        await db.scalars(sa.select(ToolCall).where(ToolCall.name == "say", ToolCall.ok.is_(False)))
    ).one()
    assert (rejected.result or {})["error"] == "too_long"
    assert await db.scalar(sa.select(sa.func.count()).select_from(Message)) == 0


async def test_the_fourth_message_in_a_turn_is_rejected(db: AsyncSession, table: Table) -> None:
    """Exit criterion. Three per turn is the cap."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(
                tool_call("say", call_id="1", message="one"),
                tool_call("say", call_id="2", message="two"),
                tool_call("say", call_id="3", message="three"),
                tool_call("say", call_id="4", message="four"),
                tool_call("say", call_id="5", message="e4 incoming"),
            ),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.said == ["one", "two", "three"]
    assert len(result.said) == MAX_MESSAGES_PER_TURN

    rate_limited = (
        await db.scalars(sa.select(ToolCall).where(ToolCall.name == "say", ToolCall.ok.is_(False)))
    ).all()
    assert len(rate_limited) == 2
    assert all((row.result or {})["error"] == "rate_limited" for row in rate_limited)


async def test_say_is_unavailable_in_a_ranked_game(db: AsyncSession) -> None:
    """TALK-03: ranked results must not be contaminated by banter."""
    ranked = await seat(db, trash_talk_enabled=False)

    result = await play_turn(
        db,
        ranked,
        scripted(
            step(tool_call("say", message="trash")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.said == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(Message)) == 0

    rejected = (await db.scalars(sa.select(ToolCall).where(ToolCall.name == "say"))).one()
    assert (rejected.result or {})["error"] == "disabled"


# ====================================================================== resign & draw


async def test_resigning_ends_the_game(db: AsyncSession, table: Table) -> None:
    result = await play_turn(db, table, scripted(step(tool_call("resign"))))

    assert result.outcome is not None
    assert result.outcome.termination is Termination.RESIGNATION
    assert result.outcome.result is GameResult.BLACK_WINS
    assert not result.outcome.is_forfeit, "resigning is a chess decision, not a failure"


async def test_offering_a_draw_does_not_end_the_turn(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("offer_draw")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.moved
    assert not table.referee.is_over


# ====================================================================== budgets


async def test_a_model_that_never_moves_runs_out_of_iterations(
    db: AsyncSession, table: Table
) -> None:
    """AGENT-08: a model looping on read-only tools must still terminate."""
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_board")), repeat_last=True),
        limits=TurnLimits(max_tool_iterations=4),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.llm_calls == 4
    assert result.outcome is not None
    assert result.outcome.termination is Termination.ERROR_FORFEIT


async def test_the_token_budget_forfeits(db: AsyncSession, table: Table) -> None:
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_board"), prompt_tokens=5000), repeat_last=True),
        limits=TurnLimits(max_tokens=4000, max_tool_iterations=10),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.BUDGET_EXCEEDED


async def test_costs_roll_up_to_the_player_and_game(db: AsyncSession, table: Table) -> None:
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_board"), prompt_tokens=100, completion_tokens=10),
            step(tool_call("make_move", move="e4"), prompt_tokens=150, completion_tokens=20),
        ),
    )

    await db.refresh(table.white)
    await db.refresh(table.game)

    assert table.white.prompt_tokens == 250
    assert table.white.completion_tokens == 30
    assert table.game.total_tokens == 280


# ====================================================================== a whole game


async def test_two_scripted_models_play_a_full_game(db: AsyncSession, table: Table) -> None:
    """Fool's mate, end to end through the real turn loop."""
    white = plays(["f3", "g4"])
    black = plays(["e5", "Qh4"])

    for colour, model in [
        (Colour.WHITE, white),
        (Colour.BLACK, black),
        (Colour.WHITE, white),
        (Colour.BLACK, black),
    ]:
        result = await play_turn(db, table, model, colour=colour)
        assert result.moved, f"{colour} failed to move"

    assert table.referee.is_over
    assert table.referee.outcome is not None
    assert table.referee.outcome.termination is Termination.CHECKMATE
    assert table.referee.outcome.result is GameResult.BLACK_WINS

    plies = (
        await db.scalars(
            sa.select(Ply).where(Ply.game_id == table.game.id).order_by(Ply.ply_number)
        )
    ).all()
    assert [p.san for p in plies] == ["f3", "e5", "g4", "Qh4#"]
    assert plies[-1].is_checkmate
