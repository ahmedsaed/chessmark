"""The board-reading scripted opponent (`agents.scripted.responsive`).

Every other helper in that module replays a fixed move list, which is enough when both sides are
scripted and useless against a **person**: a human plays whatever they like, so a canned reply is
illegal within a move or two. `responsive` reads the legal moves out of the transcript and plays
one, which is what lets the browser suite play a whole human-vs-model game for free.

The load-bearing test here is the block-wrapped content one. By the time a message reaches the
provider its content is often no longer a plain string — the prompt-caching path wraps it into
`[{"type": "text", ...}]` so a `cache_control` marker can ride along (ADR-0003). Reading it as a
string made this opponent ask for the legal moves twenty times in a row and lose a game to the
runtime's own tool-call cap, in silence.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from chessmark.agents.scripted import _legal_moves_in, responsive

TURN_PROMPT = {"role": "user", "content": "It is your move. Ply 2."}
SYSTEM = {"role": "system", "content": "You are playing a game of chess as black"}


def legal_result(*moves: str, wrapped: bool = False) -> dict[str, Any]:
    """A `get_legal_moves` tool result, in either of the two shapes that reach the provider."""
    payload = json.dumps({"count": len(moves), "moves": [{"san": move} for move in moves]})
    return {
        "role": "tool",
        "tool_call_id": "call_get_legal_moves",
        "name": "get_legal_moves",
        "content": [{"type": "text", "text": payload}] if wrapped else payload,
    }


def called(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    call = response["choices"][0]["message"]["tool_calls"][0]["function"]
    return call["name"], json.loads(call["arguments"])


async def test_it_reads_the_board_before_moving() -> None:
    name, _ = called(await responsive()(messages=[SYSTEM, TURN_PROMPT]))

    assert name == "get_legal_moves"


async def test_it_plays_a_move_from_the_result() -> None:
    messages = [SYSTEM, TURN_PROMPT, legal_result("e5", "Nf6", "a6")]

    name, args = called(await responsive()(messages=messages))

    assert name == "make_move"
    # Alphabetically first, which is arbitrary but *deterministic* — a suite that played a
    # different move each run could assert nothing about the position afterwards.
    assert args["move"] == "Nf6"


async def test_it_reads_content_wrapped_into_cache_control_blocks() -> None:
    """The regression. This is the shape a real turn actually sends (ADR-0003)."""
    messages = [SYSTEM, TURN_PROMPT, legal_result("e5", "Nf6", wrapped=True)]

    name, args = called(await responsive()(messages=messages))

    assert name == "make_move"
    assert args["move"] == "Nf6"


async def test_prefer_steers_the_game_when_the_move_is_available() -> None:
    messages = [SYSTEM, TURN_PROMPT, legal_result("e5", "Nf6")]

    _, args = called(await responsive(prefer="e5")(messages=messages))

    assert args["move"] == "e5"


async def test_prefer_is_ignored_when_the_move_is_not_legal() -> None:
    """A preference is a hint, never a licence to propose an illegal move."""
    messages = [SYSTEM, TURN_PROMPT, legal_result("e5", "Nf6")]

    _, args = called(await responsive(prefer="Qh4")(messages=messages))

    assert args["move"] == "Nf6"


async def test_an_earlier_turn_s_result_is_not_reused() -> None:
    """The transcript is the whole game, so a stale result is always in scope (ADR-0003).

    Without the turn-prompt barrier this would play a move that was legal several plies ago.
    """
    stale = [SYSTEM, legal_result("Qxf7"), TURN_PROMPT]

    assert _legal_moves_in(stale) is None

    name, _ = called(await responsive()(messages=stale))
    assert name == "get_legal_moves"


async def test_an_unreadable_result_raises_instead_of_asking_again() -> None:
    """The silent-loop guard.

    `None` (not asked yet) and `[]` (asked, unreadable) must not be treated alike. Asking again
    would fetch the same unreadable answer forever — which is precisely the bug this file exists
    to keep fixed.
    """
    broken = dict(legal_result("e5"), content="not json at all")

    assert _legal_moves_in([SYSTEM, TURN_PROMPT, broken]) == []

    with pytest.raises(RuntimeError, match="could not parse"):
        await responsive()(messages=[SYSTEM, TURN_PROMPT, broken])
