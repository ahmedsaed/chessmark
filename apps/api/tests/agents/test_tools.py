"""Tool schemas and dispatch, exercised directly against a referee.

No database and no provider — these are pure and fast.
"""

from __future__ import annotations

import json

import pytest

from chessmark.agents.tools import (
    MAX_MESSAGE_LENGTH,
    MAX_MESSAGES_PER_TURN,
    TOOL_SCHEMA_VERSION,
    ToolDispatcher,
    ToolName,
    TurnState,
    tool_schemas,
)
from chessmark.agents.types import ToolInvocation
from chessmark.game import Colour, Referee


def call(name: str, **arguments: object) -> ToolInvocation:
    raw = json.dumps(arguments)
    return ToolInvocation(id=f"call_{name}", name=name, arguments=arguments, raw_arguments=raw)


def dispatcher(
    *, referee: Referee | None = None, trash_talk: bool = True, max_retries: int = 5
) -> ToolDispatcher:
    return ToolDispatcher(
        referee=referee or Referee(),
        colour=Colour.WHITE,
        state=TurnState(),
        max_illegal_retries=max_retries,
        trash_talk_enabled=trash_talk,
    )


# ====================================================================== schemas


def test_the_schema_is_versioned() -> None:
    """Results produced under different tool surfaces are not comparable (BENCH-04)."""
    assert TOOL_SCHEMA_VERSION


def test_every_tool_is_offered() -> None:
    """Named for the set rather than its size: it was `test_all_seven_tools_are_offered`, and
    adding `claim_draw` made the name a lie in the same commit as the assertion."""
    names = {schema["function"]["name"] for schema in tool_schemas()}
    assert names == set(ToolName)


def test_say_is_withheld_from_ranked_games() -> None:
    names = {s["function"]["name"] for s in tool_schemas(trash_talk_enabled=False)}
    assert ToolName.SAY not in names
    assert names == set(ToolName) - {ToolName.SAY}


def test_schemas_are_stable_across_calls() -> None:
    """They are part of the cached prefix, so they must not vary within a game (ADR-0003)."""
    assert json.dumps(tool_schemas()) == json.dumps(tool_schemas())


def test_make_move_requires_its_argument() -> None:
    schema = next(s for s in tool_schemas() if s["function"]["name"] == ToolName.MAKE_MOVE)
    assert schema["function"]["parameters"]["required"] == ["move"]


def test_read_only_tools_require_nothing() -> None:
    for name in (ToolName.GET_BOARD, ToolName.GET_LEGAL_MOVES, ToolName.RESIGN):
        schema = next(s for s in tool_schemas() if s["function"]["name"] == name)
        assert schema["function"]["parameters"]["required"] == []


# ====================================================================== reading


def test_get_board_describes_the_position() -> None:
    result = dispatcher().execute(call(ToolName.GET_BOARD))

    assert result.ok
    payload = result.payload
    assert payload["side_to_move"] == "white"
    assert payload["you_are"] == "white"
    assert payload["legal_move_count"] == 20
    assert payload["in_check"] is False
    assert payload["material"]["balance"] == 0
    assert "a b c d e f g h" in payload["board"]
    assert payload["fen"].startswith("rnbqkbnr")


def test_get_legal_moves_lists_everything_with_flags() -> None:
    referee = Referee(start_fen="6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1")
    result = dispatcher(referee=referee).execute(call(ToolName.GET_LEGAL_MOVES))

    moves = {move["san"]: move for move in result.payload["moves"]}
    assert result.payload["count"] == len(moves)
    assert moves["Ra8#"]["checkmate"] is True
    assert moves["Ra8#"]["check"] is True
    assert "capture" not in moves["Ra2"]


def test_get_move_history_can_be_truncated() -> None:
    referee = Referee()
    for move in ["e4", "e5", "Nf3", "Nc6"]:
        referee.play(move)

    dispatch = dispatcher(referee=referee)
    assert dispatch.execute(call(ToolName.GET_MOVE_HISTORY)).payload["moves"] == [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
    ]
    assert dispatch.execute(call(ToolName.GET_MOVE_HISTORY, last_n=2)).payload["moves"] == [
        "Nf3",
        "Nc6",
    ]


def test_reading_tools_never_change_the_board() -> None:
    referee = Referee()
    dispatch = dispatcher(referee=referee)

    for name in (ToolName.GET_BOARD, ToolName.GET_LEGAL_MOVES, ToolName.GET_MOVE_HISTORY):
        dispatch.execute(call(name))

    assert referee.ply == 0


# ====================================================================== moving


def test_a_legal_move_is_played() -> None:
    referee = Referee()
    result = dispatcher(referee=referee).execute(call(ToolName.MAKE_MOVE, move="e4"))

    assert result.ok
    assert result.ends_turn
    assert result.payload["played"] == "e4"
    assert result.payload["uci"] == "e2e4"
    assert referee.ply == 1


def test_an_illegal_move_returns_the_full_legal_list() -> None:
    result = dispatcher().execute(call(ToolName.MAKE_MOVE, move="Qh5"))

    assert not result.ok
    assert result.illegal
    assert len(result.payload["legal_moves_san"]) == 20
    assert result.payload["attempt"] == 1
    assert result.payload["attempts_remaining"] == 4
    assert result.payload["detail"]


def test_checkmate_is_reported_on_the_move() -> None:
    referee = Referee(start_fen="6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1")
    result = dispatcher(referee=referee).execute(call(ToolName.MAKE_MOVE, move="Ra8#"))

    assert result.payload["game_over"] is True
    assert result.payload["termination"] == "checkmate"
    assert result.ends_game


def test_a_missing_move_argument_is_treated_as_illegal() -> None:
    result = dispatcher().execute(call(ToolName.MAKE_MOVE))

    assert result.illegal
    assert result.payload["legal_moves_san"]


def test_retries_are_exhausted_only_past_the_limit() -> None:
    """Five failures are survivable with max_retries=5; the sixth is not."""
    dispatch = dispatcher(max_retries=5)

    for _ in range(5):
        dispatch.execute(call(ToolName.MAKE_MOVE, move="Qh5"))
    assert not dispatch.retries_exhausted

    dispatch.execute(call(ToolName.MAKE_MOVE, move="Qh5"))
    assert dispatch.retries_exhausted


# ====================================================================== resign / draw


def test_resigning_ends_the_game() -> None:
    referee = Referee()
    result = dispatcher(referee=referee).execute(call(ToolName.RESIGN))

    assert result.ends_game
    assert referee.is_over
    assert result.payload["result"] == "0-1"


def test_offering_a_draw_changes_nothing_yet() -> None:
    referee = Referee()
    result = dispatcher(referee=referee).execute(call(ToolName.OFFER_DRAW))

    assert result.ok
    assert not result.ends_turn
    assert not referee.is_over


# ====================================================================== say


def test_a_message_is_accepted_and_trimmed() -> None:
    result = dispatcher().execute(call(ToolName.SAY, message="  Nice try.  "))

    assert result.ok
    assert result.message == "Nice try."


@pytest.mark.parametrize("message", ["", "   ", None, 42])
def test_an_empty_or_non_string_message_is_rejected(message: object) -> None:
    result = dispatcher().execute(call(ToolName.SAY, message=message))

    assert not result.ok
    assert result.payload["error"] == "invalid_arguments"


def test_an_overlong_message_is_rejected() -> None:
    result = dispatcher().execute(call(ToolName.SAY, message="x" * (MAX_MESSAGE_LENGTH + 1)))

    assert not result.ok
    assert result.payload["error"] == "too_long"
    assert result.message is None


def test_a_message_at_exactly_the_limit_is_accepted() -> None:
    assert dispatcher().execute(call(ToolName.SAY, message="x" * MAX_MESSAGE_LENGTH)).ok


def test_the_fourth_message_in_a_turn_is_rate_limited() -> None:
    dispatch = dispatcher()

    for index in range(MAX_MESSAGES_PER_TURN):
        assert dispatch.execute(call(ToolName.SAY, message=f"message {index}")).ok

    rejected = dispatch.execute(call(ToolName.SAY, message="one too many"))
    assert not rejected.ok
    assert rejected.payload["error"] == "rate_limited"


def test_say_is_refused_in_a_ranked_game() -> None:
    result = dispatcher(trash_talk=False).execute(call(ToolName.SAY, message="trash"))

    assert not result.ok
    assert result.payload["error"] == "disabled"


# ====================================================================== bad input


def test_an_unknown_tool_lists_what_is_available() -> None:
    result = dispatcher().execute(call("teleport_king"))

    assert not result.ok
    assert result.payload["error"] == "unknown_tool"
    assert ToolName.MAKE_MOVE in result.payload["detail"]


def test_an_unknown_tool_in_a_ranked_game_does_not_advertise_say() -> None:
    result = dispatcher(trash_talk=False).execute(call("teleport_king"))
    assert ToolName.SAY not in result.payload["detail"]


def test_unparseable_arguments_are_reported_not_raised() -> None:
    """A model emitting broken JSON is a finding to count (AGENT-01)."""
    broken = ToolInvocation(
        id="1",
        name=ToolName.MAKE_MOVE,
        arguments={},
        raw_arguments="{move: e4",
        parse_error="invalid JSON",
    )

    result = dispatcher().execute(broken)

    assert not result.ok
    assert result.payload["error"] == "invalid_arguments"
    assert not result.illegal, "a parse failure is a tool error, not a chess error"


def test_the_tool_counter_tracks_every_call() -> None:
    dispatch = dispatcher()
    for _ in range(3):
        dispatch.execute(call(ToolName.GET_BOARD))

    assert dispatch.state.tool_calls == 3
