"""A scripted stand-in for a model.

The key testing primitive of the agent runtime, and the reason Phase 4 can be verified exhaustively
without spending anything. It plugs in as `LlmGateway(completion_fn=...)`, so tests exercise the
*real* path — normalisation, cost, redaction, persistence — with only the provider replaced.

    gateway = LlmGateway(completion_fn=scripted(
        tool_call("make_move", move="Qh5"),   # illegal here
        tool_call("make_move", move="e4"),    # legal
    ))

Also useful for local development: it lets a whole game run with no API key.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterable
from typing import Any

CompletionFn = Callable[..., Any]


def tool_call(name: str, *, call_id: str | None = None, **arguments: Any) -> dict[str, Any]:
    """One tool call in a scripted response."""
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def raw_tool_call(name: str, arguments: str, *, call_id: str | None = None) -> dict[str, Any]:
    """A tool call with deliberately unparseable arguments, to exercise the failure path."""
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def step(
    *calls: dict[str, Any],
    content: str | None = None,
    reasoning: str | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
    finish_reason: str | None = None,
    cost: float = 0.0,
) -> dict[str, Any]:
    """One scripted provider response.

    `cost` is what OpenRouter reports it charged. Zero by default, because most tests care about
    behaviour rather than money — but the budget tests need a turn that actually costs something,
    and inventing the number here is better than making them stand up a pricing table.
    """
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = list(calls)

    return {
        "id": "gen-scripted",
        "model": "scripted/model",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason or ("tool_calls" if calls else "stop"),
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "cost": cost,
        },
    }


def says(message: str, *calls: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Shorthand: talk, then act."""
    return step(tool_call("say", message=message), *calls, **kwargs)


def prose(content: str, **kwargs: Any) -> dict[str, Any]:
    """A response with no tool call at all — the thing AGENT-01 refuses to act on."""
    return step(content=content, **kwargs)


def scripted(*steps: dict[str, Any], repeat_last: bool = False) -> CompletionFn:
    """A `completion_fn` returning each scripted response in order.

    Running past the end raises by default, which makes an unexpected extra LLM call a loud test
    failure rather than a silent hang. `repeat_last=True` keeps returning the final response, for
    loops whose length is the thing under test.
    """
    queue = list(steps)
    calls: list[dict[str, Any]] = []

    async def _complete(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        index = len(calls) - 1

        if index < len(queue):
            return queue[index]
        if repeat_last and queue:
            return queue[-1]

        msg = (
            f"the script ran out: {len(queue)} responses were provided but the turn made "
            f"{len(calls)} calls. Add a step, or pass repeat_last=True."
        )
        raise AssertionError(msg)

    _complete.calls = calls  # type: ignore[attr-defined]
    return _complete


def plays(moves: Iterable[str], *, per_move_tokens: int = 100, cost: float = 0.0) -> CompletionFn:
    """A model that simply plays the given moves in order, one per turn.

    The workhorse for orchestration tests: two of these can play a whole scripted game.
    """
    sequence = itertools.cycle([step(tool_call("make_move", move=m), cost=cost) for m in moves])
    calls: list[dict[str, Any]] = []

    async def _complete(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return next(sequence)

    _complete.calls = calls  # type: ignore[attr-defined]
    del per_move_tokens
    return _complete


def alternating(
    white_moves: Iterable[str], black_moves: Iterable[str], *, cost: float = 0.0
) -> CompletionFn:
    """One completion function serving both sides of a game.

    The worker calls the gateway once per turn without saying whose turn it is, so this decides
    from the system prompt at the head of the transcript. Lets a whole scripted game run through
    the real orchestration path with a single injected function.
    """
    white = plays(white_moves, cost=cost)
    black = plays(black_moves, cost=cost)

    async def _complete(**kwargs: Any) -> Any:
        messages = kwargs.get("messages") or [{}]
        system = str(messages[0].get("content", ""))
        return await (white if "as white" in system.lower() else black)(**kwargs)

    return _complete


def responsive(
    *, cost: float = 0.0, prefer: str | None = None, reasoning: str | None = None
) -> CompletionFn:
    """A scripted opponent that reads the board instead of replaying a list.

    Every other helper here plays a fixed sequence, which is enough for a game whose *other* side is
    also scripted. It is not enough to answer a **person**: a human plays whatever they like, so a
    canned reply is illegal within a move or two.

    This one takes the turn in two round-trips, exactly as a real model does — ask for the legal
    moves, then play one of them. The choice is the alphabetically first SAN, which is arbitrary but
    **deterministic**: a browser suite that plays a different move each run cannot assert anything
    about the position afterwards.

    `prefer` names a move to take when it is available, so a test can steer the game somewhere
    specific — a checkmate, a capture — without scripting every ply around it.

    `reasoning` makes it think out loud. Without it there is nothing for invariant 8 to be about:
    a test that reasoning is hidden mid-game proves nothing against a model that never produced
    any, and would pass just as happily if the gate were removed.
    """

    async def _complete(**kwargs: Any) -> Any:
        moves = _legal_moves_in(kwargs.get("messages") or [])

        if moves is None:
            # Nothing has been read yet this turn, so read.
            return step(tool_call("get_legal_moves"), cost=cost)

        if not moves:
            # A result *was* there and could not be read. Asking again would produce the same
            # unreadable answer, so this would loop until the runtime's own cap stopped it —
            # which is what happened once, quietly, for twenty calls. Fail loudly instead.
            raise RuntimeError(
                "scripted opponent read a get_legal_moves result it could not parse; "
                "the tool-result shape has changed"
            )

        chosen = prefer if prefer in moves else sorted(moves)[0]
        return step(tool_call("make_move", move=chosen), cost=cost, reasoning=reasoning)

    return _complete


def _text_of(content: Any) -> str:
    """The text of a message whose content may have been wrapped into blocks.

    By the time a message reaches the provider its content is often no longer a plain string: the
    prompt-caching path wraps it into `[{"type": "text", "text": ...}]` so a `cache_control` marker
    can ride along (ADR-0003). Reading `str(content)` then yields the repr of a list, `json.loads`
    raises, and this opponent silently falls back to asking for the legal moves again — forever.
    That is exactly what happened, and it cost a whole game of 20 identical tool calls.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return ""


def _legal_moves_in(messages: list[dict[str, Any]]) -> list[str] | None:
    """The moves from the most recent `get_legal_moves` result, if this turn has asked.

    `None` means this turn has not asked yet; an empty list means it asked and the answer could
    not be read. The caller needs to tell those apart — treating both as "ask again" is what turns
    a parsing bug into an unbounded loop.

    Scans backwards and stops at the turn prompt, so a result from an *earlier* turn — the
    transcript is the whole game (ADR-0003) — cannot be mistaken for this one and produce a move
    that was legal several plies ago.
    """
    for message in reversed(messages):
        if message.get("role") == "user":
            return None
        if message.get("role") != "tool" or message.get("name") != "get_legal_moves":
            continue
        try:
            payload = json.loads(_text_of(message.get("content")))
        except ValueError:
            return []
        found = payload.get("moves")
        if not isinstance(found, list):
            return []
        return [m["san"] for m in found if isinstance(m, dict) and "san" in m]
    return None


__all__ = [
    "alternating",
    "plays",
    "prose",
    "raw_tool_call",
    "responsive",
    "says",
    "scripted",
    "step",
    "tool_call",
]
