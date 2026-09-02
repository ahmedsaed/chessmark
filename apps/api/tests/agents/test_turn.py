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
from chessmark.agents.turn import MAX_NUDGES, TurnLimits
from chessmark.db.enums import EventType, TurnStatus
from chessmark.db.models import GameEvent, LlmCall, Message, Ply, ToolCall, Turn
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


async def test_prose_is_nudged_and_then_forfeits(db: AsyncSession, table: Table) -> None:
    """Exit criterion. AGENT-01 forbids reading a move out of prose.

    `MAX_NUDGES` nudges, then the next toolless reply ends the turn — the same shape as
    `MAX_TRUNCATIONS`. It was one nudge, and one instruction turned out to be a thin basis for
    recording a loss: the first prose reply is often a reasoning model narrating before it acts.
    """
    result = await play_turn(
        db,
        table,
        scripted(*[prose(f"Still just talking ({n}).") for n in range(MAX_NUDGES + 1)]),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.ERROR_FORFEIT
    assert f"{MAX_NUDGES + 1} times in a row" in result.outcome.detail
    assert not result.moved
    assert table.referee.is_over


async def test_prose_short_of_the_budget_does_not_forfeit(db: AsyncSession, table: Table) -> None:
    """The point of raising it. A model that talks twice and then plays has not failed at
    anything — it narrated, was told twice that prose does not move a piece, and moved."""
    result = await play_turn(
        db,
        table,
        scripted(
            prose("Let me think about this."),
            prose("Considering the Italian."),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.moved


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


# ====================================================================== a loop, broken


async def test_a_third_identical_read_is_answered_with_a_nudge(
    db: AsyncSession, table: Table
) -> None:
    """**The loop this exists to break.** `nemotron-3-super-120b` called `get_move_history` with no
    arguments twenty times in one turn, got a byte-identical result each time, emitted the same
    2,303-token reasoning each time, and lost a ranked game to `max_tool_iterations` — 1.96 million
    prompt tokens and twenty of a thousand daily free requests for a ply that never happened.

    Nothing broke it because nothing could: the exchange was deterministic, so handing back the same
    answer again was guaranteed to produce the same call. The third answer is therefore a different
    one, and it says how many rounds are left, because a model that cannot see it is running out
    cannot act on it.
    """
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_move_history")), repeat_last=True),
        limits=TurnLimits(max_tool_iterations=5),
    )

    calls = (
        await db.scalars(
            sa.select(ToolCall).where(ToolCall.game_id == table.game.id).order_by(ToolCall.sequence)
        )
    ).all()

    assert [c.ok for c in calls] == [True, True, False, False, False]
    assert all(c.name == "get_move_history" for c in calls)

    nudge = calls[2].result
    assert nudge["error"] == "repeated_call"
    assert "already called `get_move_history`" in nudge["detail"]
    assert "2 tool rounds left" in nudge["detail"], "the count has to be actionable, not decorative"

    # And it is still a forfeit. The harness declined to answer; it did not excuse the model.
    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.ERROR_FORFEIT


async def test_two_identical_reads_are_answered_normally(db: AsyncSession, table: Table) -> None:
    """Deliberately generous. A model just refused an illegal move may reasonably re-read the board,
    and the position genuinely has not changed — what it may not do is ask a third time."""
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_board")),
            step(tool_call("get_board")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    calls = (
        await db.scalars(
            sa.select(ToolCall).where(ToolCall.game_id == table.game.id).order_by(ToolCall.sequence)
        )
    ).all()

    assert [c.ok for c in calls] == [True, True, True]
    assert all(c.result.get("error") != "repeated_call" for c in calls)


async def test_different_questions_are_not_repeats(db: AsyncSession, table: Table) -> None:
    """The signature is the name *and* the arguments together, so three different reads are three
    questions. Keyed on the name alone this would have refused a model that read the board, listed
    its moves and checked the history — the ordinary way to play a turn."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_board")),
            step(tool_call("get_legal_moves")),
            step(tool_call("get_move_history")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.moved
    calls = (await db.scalars(sa.select(ToolCall).where(ToolCall.game_id == table.game.id))).all()
    assert all(c.result.get("error") != "repeated_call" for c in calls)


async def test_a_repeated_move_is_left_to_the_illegal_move_rule(
    db: AsyncSession, table: Table
) -> None:
    """`make_move` is not read-only, and a repeated one is an illegal-move retry. ADR-0002 already
    answers those with the full legal move list five times over; intercepting them here would take
    a rule away from the module that owns it and cut the retries short."""
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="Qh8")), repeat_last=True),
        limits=TurnLimits(max_tool_iterations=10),
    )

    assert result.outcome is not None
    assert result.outcome.termination is Termination.ILLEGAL_MOVE_FORFEIT
    assert result.illegal_attempts == 6, "the first try and all five retries, not two"


async def test_a_runaway_output_stops_the_turn(db: AsyncSession, table: Table) -> None:
    """The budget counts what the model *generated*. 400k tokens of output in one turn is
    pathological and worth stopping."""
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_board"), completion_tokens=5000), repeat_last=True),
        limits=TurnLimits(max_completion_budget=4000, max_tool_iterations=10),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.BUDGET_EXCEEDED


async def test_a_harness_bound_does_not_mark_the_seat_forfeited(
    db: AsyncSession, table: Table
) -> None:
    """`Player.forfeited` is *published* — it is the leaderboard's forfeits column — and it was
    written by the turn's status rather than by the ending (ADR-0024).

    `BUDGET_EXCEEDED` travels as `TurnStatus.FORFEITED` because it does end the game, but
    `ratable.HARNESS_TERMINATIONS` says plainly that it is not a finding. Two games in the free
    pool were budget-stopped, reopened, and played on to a real checkmate and a real threefold
    draw; both stayed ratable and both carried a forfeit their model never earned.
    """
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_board"), completion_tokens=5000), repeat_last=True),
        limits=TurnLimits(max_completion_budget=4000, max_tool_iterations=10),
    )

    assert result.outcome is not None
    assert result.outcome.termination is Termination.BUDGET_EXCEEDED

    await db.refresh(table.white)
    assert not table.white.forfeited, "our own ceiling is not a forfeit against the player"


async def test_a_real_forfeit_still_marks_the_seat(db: AsyncSession, table: Table) -> None:
    """The other half, or the fix above would simply empty the column."""
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("get_board")), repeat_last=True),
        limits=TurnLimits(max_tool_iterations=4),
    )

    assert result.outcome is not None
    assert result.outcome.termination is Termination.ERROR_FORFEIT

    await db.refresh(table.white)
    assert table.white.forfeited, "a model that never called a tool did forfeit"


async def test_replaying_the_prompt_is_not_the_model_s_spending(
    db: AsyncSession, table: Table
) -> None:
    """The regression that matters, and it ended three real games.

    The transcript is re-sent on every round-trip (ADR-0003), so counting prompt tokens measured
    transcript size times round-trips — both the harness's doing. A model that produced 5,263
    tokens was forfeited for "using 514,446": four replays of a 128k transcript. It punished long
    games hardest, because that is where the transcript is largest.
    """
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_board"), prompt_tokens=200_000),
            step(tool_call("get_legal_moves"), prompt_tokens=200_000),
            step(tool_call("make_move", move="e4"), prompt_tokens=200_000),
        ),
        limits=TurnLimits(max_completion_budget=4000),
    )

    assert result.prompt_tokens >= 600_000, "the prompt really was replayed three times"
    assert result.status is TurnStatus.COMPLETED
    assert result.moved


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


async def test_every_call_carries_a_completion_token_cap(db: AsyncSession, table: Table) -> None:
    """Found live: a reasoning model spent 34,260 tokens on one move and never emitted a tool
    call. The per-turn budget cannot prevent that — it is only checked between round-trips, by
    which point the tokens are spent. Only a per-call cap bounds it."""
    model = scripted(step(tool_call("make_move", move="e4")))

    await play_turn(db, table, model, limits=TurnLimits(max_completion_tokens=1234))

    request = model.calls[0]  # type: ignore[attr-defined]
    assert request["max_tokens"] == 1234


async def test_the_default_cap_is_applied_without_being_asked(
    db: AsyncSession, table: Table
) -> None:
    model = scripted(step(tool_call("make_move", move="e4")))

    await play_turn(db, table, model)

    assert model.calls[0]["max_tokens"] == TurnLimits().max_completion_tokens  # type: ignore[attr-defined]
    assert TurnLimits().max_completion_tokens < 200_000, (
        "the cap must be tighter than the turn budget"
    )


async def test_a_provider_failure_does_not_forfeit_the_model(
    db: AsyncSession, table: Table
) -> None:
    """AGENT-09. Found live: an OpenRouter daily quota ended a turn mid-game. Forfeiting there
    would record our infrastructure problem as the model failing to operate, and hand the
    opponent a win on the leaderboard."""

    class RateLimitError(Exception):
        status_code = 429

    async def always_rate_limited(**_kwargs: object) -> object:
        raise RateLimitError

    result = await play_turn(db, table, always_rate_limited)

    assert result.status is TurnStatus.FAILED
    assert result.error is not None
    assert "429" in result.error
    assert result.outcome is None, "a provider failure must not decide the game"
    assert not table.referee.is_over, "the game stays open for the orchestrator to retry"

    await db.refresh(table.white)
    assert table.white.forfeited is False
    assert table.white.illegal_attempts == 0, "provider errors are not illegal moves"


# ====================================================================== truncation


async def test_a_truncated_response_is_not_treated_as_a_refusal(
    db: AsyncSession, table: Table
) -> None:
    """Found live. gpt-oss-20b:free spent 32,753 reasoning tokens, hit its provider's output
    ceiling mid-thought, and was forfeited for "replying without calling a tool" — a refusal it
    never made. It never reached the point of acting. Blaming the model for an output budget it
    does not control puts a harness limit into the benchmark result."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(content="thinking...", finish_reason="length"),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.moved
    assert not table.referee.is_over


async def test_a_truncated_response_gets_a_prompt_that_says_so(
    db: AsyncSession, table: Table
) -> None:
    """Telling a truncated model "you did not call a tool" would simply be untrue."""
    from chessmark.agents import transcript

    await play_turn(
        db,
        table,
        scripted(
            step(content="thinking...", finish_reason="length"),
            step(tool_call("make_move", move="e4")),
        ),
    )

    messages = await transcript.build_messages(db, table.white.id)
    prompts_sent = [str(m.get("content", "")) for m in messages if m["role"] == "user"]

    assert any("cut off by the output limit" in text for text in prompts_sent)
    assert not any("did not call a tool" in text for text in prompts_sent)


async def test_repeated_truncation_fails_the_turn_rather_than_the_player(
    db: AsyncSession, table: Table
) -> None:
    """It used to forfeit under `TRUNCATED`. The output budget that ran out belongs to the harness
    or the endpoint and never to the weights, so the turn fails and the worker decides — the same
    treatment a provider outage gets (ADR-0024)."""
    result = await play_turn(
        db,
        table,
        scripted(step(content="thinking...", finish_reason="length"), repeat_last=True),
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "nothing is recorded against either player"
    assert result.error


async def test_truncation_and_refusal_have_separate_budgets(db: AsyncSession, table: Table) -> None:
    """A truncation must not consume the single prose nudge, or a model that is cut off once and
    then declines once would forfeit having only actually refused a single time."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(content="cut off", finish_reason="length"),
            prose("I'll play e4."),
            step(tool_call("make_move", move="e4")),
        ),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.moved


async def test_reasoning_streams_live_in_a_model_vs_model_game(
    db: AsyncSession, table: Table
) -> None:
    """Invariant 8 is about participants, not spectators. Neither model can read this stream —
    each sees only its own transcript — so live reasoning leaks nothing and is the whole appeal."""
    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), reasoning="Control the centre.")),
    )

    events = (
        await db.scalars(sa.select(GameEvent).where(GameEvent.type == EventType.THINKING))
    ).all()

    assert events
    assert events[0].payload["reasoning"] == "Control the centre."


async def test_a_human_game_records_its_reasoning_like_any_other(
    db: AsyncSession, db_human_table: Table
) -> None:
    """The log is written in full even when a person is playing.

    Withholding it from that person is a *read* concern and lives in `api/redaction.py`; the gate
    used to run here instead, and because `game_events` is append-only (ADR-0008) that made the
    omission permanent — a person's own games were the only ones whose reasoning the transcript
    could never show, long after there was anything left to leak.

    `tests/api/test_human_play.py` covers the other half: that neither read path serves this text
    to a human while their game is live.
    """
    await play_turn(
        db,
        db_human_table,
        scripted(
            step(
                tool_call("make_move", move="e4"),
                reasoning="I plan Qh5 next.",
                reasoning_tokens=42,
            )
        ),
    )

    events = (
        await db.scalars(sa.select(GameEvent).where(GameEvent.type == EventType.THINKING))
    ).all()

    assert events
    assert events[0].payload["reasoning"] == "I plan Qh5 next."
    assert events[0].payload["tokens"] > 0
