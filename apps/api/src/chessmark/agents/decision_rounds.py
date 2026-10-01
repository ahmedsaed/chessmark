"""Asking a decision model for its turn, in heats when it cannot take every move at once (ADR-0059).

A model with no limit on how many options one question may carry is asked exactly as `d2` asked
it: one request, the move and the action. A model with a limit — Tev takes 20, Solar 26, and a
middlegame has 30 to 40 legal moves — is asked its move in **heats** of at most its limit, and then
in a **final** between the heat winners. Should there be more heat winners than the limit, they
play another round of heats first, so any position fits any limit of two or more.

The action question rides with the first request, because what to do with the turn is asked once
and does not depend on which move wins. Every answer is read — and so validated — before the
caller acts on any of it, so a malformed final leaves nothing half-done.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from chessmark.agents.decision_request import (
    ACTION_QUESTION,
    MOVE_QUESTION,
    DecisionRequest,
    heat_key,
    split,
)
from chessmark.agents.decisions import Decision

#: Sends one request's questions and returns the answer. The caller's, so it can record each call
#: as it is made — a heat already paid for is a call in the record even if the final then fails.
Ask = Callable[[dict[str, Any]], Awaitable[Decision]]


@dataclass(frozen=True, slots=True)
class Heat:
    """One heat as it was asked and answered."""

    round: int
    choice: str
    #: Every move in the heat, most likely first, as the event stores a ranking.
    probabilities: list[list[Any]]


@dataclass(slots=True)
class Answer:
    """A whole turn's answer, read from however many requests it took."""

    #: The move the model chose — for a forced move, the only legal one, which it was not asked.
    choice: str
    #: The deciding question's distribution: the only question without heats, the final with them,
    #: and empty for a forced move, which asked no move question at all.
    probabilities: dict[str, float]
    confidence: float | None
    action: str
    answers: dict[str, float]
    heats: list[Heat] = field(default_factory=list)
    forced: bool = False


def _ranked(probabilities: dict[str, float]) -> list[list[Any]]:
    return [[san, p] for san, p in sorted(probabilities.items(), key=lambda kv: (-kv[1], kv[0]))]


async def decide(request: DecisionRequest, *, max_choices: int | None, ask: Ask) -> Answer:
    """The model's move and action for `request`, asked within `max_choices` options a question.

    `None` is no limit. The check never records one below six, so a smaller one is a bug.
    """
    if max_choices is not None and max_choices < 3:
        # Heats of one would never shrink the field, and the loop below would not end; heats of
        # two can leave one heat a single move, which a host that needs two options refuses.
        raise ValueError(f"cannot ask in heats of {max_choices}")
    moves = list(request.moves)
    actions = set(request.actions)

    if len(moves) == 1:
        decision = await ask(request.questions)
        action, answers = decision.choice(ACTION_QUESTION, actions)
        return Answer(
            choice=moves[0],
            probabilities={},
            confidence=None,
            action=action,
            answers=answers,
            forced=True,
        )

    if max_choices is None or len(moves) <= max_choices:
        decision = await ask(request.questions)
        choice, probabilities = decision.choice(MOVE_QUESTION, set(moves))
        action, answers = decision.choice(ACTION_QUESTION, actions)
        return Answer(
            choice=choice,
            probabilities=probabilities,
            confidence=decision.confidence(MOVE_QUESTION),
            action=action,
            answers=answers,
        )

    heats: list[Heat] = []
    action_answer: tuple[str, dict[str, float]] | None = None
    candidates = moves
    round_number = 1
    while len(candidates) > max_choices:
        groups = split(candidates, max_choices)
        decision = await ask(request.heats(groups, with_action=action_answer is None))
        if action_answer is None:
            action_answer = decision.choice(ACTION_QUESTION, actions)
        winners: list[str] = []
        for index, group in enumerate(groups):
            winner, probabilities = decision.choice(heat_key(index), set(group))
            winners.append(winner)
            heats.append(Heat(round_number, winner, _ranked(probabilities)))
        candidates = winners
        round_number += 1

    decision = await ask(request.final(candidates))
    choice, probabilities = decision.choice(MOVE_QUESTION, set(candidates))
    assert action_answer is not None  # the loop ran at least once: len(moves) > max_choices
    action, answers = action_answer
    return Answer(
        choice=choice,
        probabilities=probabilities,
        confidence=decision.confidence(MOVE_QUESTION),
        action=action,
        answers=answers,
        heats=heats,
    )


__all__ = ["Answer", "Ask", "Heat", "decide"]
