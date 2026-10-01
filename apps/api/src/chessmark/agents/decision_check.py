"""Whether a decision model can answer what we ask, found out once, before it is ever paired.

**Nothing in the catalogue says which question types a decision model accepts** (ADR-0051).
Span-01 and Jev list identical metadata — the same modality, empty `supported_parameters` — and yet
Span refuses anything but yes-or-no questions over a conversation:

    "Respan only accepts noul questions whose instructions and criteria are plain strings"

**Nor how many options one question may carry** (ADR-0059). Tev takes 20 and Solar 26, and a
middlegame has 30 to 40 legal moves. Paired blind, such a model is refused at ply 0 of every game
it is given, and each refusal is an abandoned pairing in the record.

So a new model is asked in exactly the shape a real turn sends, over the busiest position chess
has: 218 legal moves. One that answers has no limit worth knowing. One that is refused is asked
again with each question **halved**, at most six requests in all — 218, 109, 54, 27, 13, 6 — and
the largest it answered is written on its registry row as `decisions_max_choices`; its turns are
then asked in heats of at most that many. Each smaller request is shaped as the heats a turn under
that limit would send, every question at exactly the size, so it tests the number of questions at
once as well as their size. A model refused even at six is refused, with its host's own sentence.

**Once per model per `DECISION_VERSION`**, and never on a routine refresh: a new version may ask a
question a host treats differently, and nothing else can change what it accepts. A failure that is
about the moment rather than the model — a rate limit, an outage, an empty account — records
nothing, and the next refresh asks again. A refusal is not billed; an answer costs a few thousand
input tokens, a fraction of a cent.

This is a capability check, not calibration. `d2` has no thresholds to tune (ADR-0051), so a model
that can answer the question is ready to play.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import chess
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import (
    ACTION_QUESTION,
    DECISION_VERSION,
    MOVE_QUESTION,
    build_request,
    heat_key,
)
from chessmark.agents.decisions import DecisionGateway, MalformedDecisionError
from chessmark.agents.types import LlmError
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import ModelRegistry

log = logging.getLogger(__name__)

#: The position the check asks about: the most legal moves any chess position has, 218 — eight
#: white queens and the rest — reached by Black's last move, so the request carries history and a
#: last move and every field a real turn sends is present in it. A model that takes every move of
#: this position in one question takes any position's.
BEFORE_CHECK = "R6R/3Q4/1Q4Q1/4Q3/2Q4Q/Qp3Q2/p2Q4/kBNN1KB1 b - - 0 1"
CHECK_MOVE = "b2"

#: The sizes asked, largest first, each half the one before: six requests at most per model. The
#: first is every legal move of `BEFORE_CHECK` in one question, and answering it means no limit.
PROBE_SIZES = (218, 109, 54, 27, 13, 6)

#: How much of a host's refusal is kept. The message is the useful part — it says what the host
#: does accept — and a whole error body would be noise on a registry row.
REFUSAL_LENGTH = 500


@dataclass(slots=True)
class CheckReport:
    answered: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: Could not be asked this time — rate-limited, unreachable — and will be asked again.
    deferred: list[str] = field(default_factory=list)
    #: The limit found for each answering model that has one.
    limits: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"{len(self.answered)} answered, {len(self.refused)} refused, "
            f"{len(self.deferred)} deferred"
        )


def _host_message(raw: str) -> str:
    """The host's own sentence out of an error body, which is the part that says what it accepts."""
    try:
        body = json.loads(raw)
    except ValueError:
        return raw
    message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    return str(message) if message else raw


def check_board() -> chess.Board:
    board = chess.Board(BEFORE_CHECK)
    board.push_san(CHECK_MOVE)
    return board


def check_request(model: str, size: int) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """The request asked at `size`, and the options each of its questions must be answered from.

    At the full size it is a turn's own request. Below it, it is the first round of heats a turn
    under that limit would send — but with **every** heat at exactly `size`, the last one filled
    out from the start of the list, so the host is shown its largest question as many times as a
    real turn could ask it. Even heats in a real turn are never larger.
    """
    request = build_request(check_board())
    moves = list(request.moves)
    if size >= len(moves):
        return request.body(model=model), {
            MOVE_QUESTION: set(moves),
            ACTION_QUESTION: set(request.actions),
        }
    count = -(-len(moves) // size)
    groups = [[moves[(i * size + j) % len(moves)] for j in range(size)] for i in range(count)]
    questions = request.heats(groups, with_action=True)
    expected = {heat_key(i): set(group) for i, group in enumerate(groups)}
    expected[ACTION_QUESTION] = set(request.actions)
    return request.body(model=model, questions=questions), expected


class _DeferredError(Exception):
    """The check could not be asked this time; nothing about the model is known."""


async def _probe(gateway: DecisionGateway, slug: str) -> tuple[int | None, str | None]:
    """The model's limit (`None` for none) and `None`, or no limit and the host's last refusal."""
    refusal = ""
    for size in PROBE_SIZES:
        body, expected = check_request(slug, size)
        try:
            decision = await gateway.decide(body)
            for key, options in expected.items():
                decision.choice(key, options)
        except LlmError as error:
            if not error.request_rejected:
                raise _DeferredError(str(error)) from error
            refusal = _host_message(error.message)
            continue
        except MalformedDecisionError as error:
            # It answered, but not with an answer to our question — as unusable in a game as a
            # refusal, and for the same reason. A smaller question may still be answered.
            refusal = f"answered with something unusable: {error}"
            continue
        return (None if size == PROBE_SIZES[0] else size), None
    return None, refusal


async def check_decision_models(
    session: AsyncSession, gateway: DecisionGateway, *, recheck: bool = False
) -> CheckReport:
    """Ask every decision model not yet checked under this version whether it can answer.

    `recheck` asks them all again, for an operator who believes a host has changed what it accepts.
    """
    query = sa.select(ModelRegistry).where(
        ModelRegistry.runtime == ModelRuntime.DECISION, ModelRegistry.enabled.is_(True)
    )
    if not recheck:
        query = query.where(
            sa.or_(
                ModelRegistry.decisions_checked.is_(None),
                ModelRegistry.decisions_checked != DECISION_VERSION,
            )
        )

    report = CheckReport()
    for model in list(await session.scalars(query.order_by(ModelRegistry.openrouter_id))):
        try:
            limit, refusal = await _probe(gateway, model.openrouter_id)
        except _DeferredError as error:
            # About the moment, not the model: nothing is recorded, the next refresh asks again.
            log.warning("decision check deferred for %s: %s", model.openrouter_id, error)
            report.deferred.append(model.openrouter_id)
            continue

        model.decisions_checked = DECISION_VERSION
        model.decisions_max_choices = limit
        if refusal is None:
            model.decisions_refusal = None
            report.answered.append(model.openrouter_id)
            if limit is not None:
                report.limits[model.openrouter_id] = limit
            continue

        model.decisions_refusal = refusal[:REFUSAL_LENGTH]
        log.warning("%s cannot answer our decision requests: %s", model.openrouter_id, refusal)
        report.refused.append(model.openrouter_id)

    await session.flush()
    return report


__all__ = [
    "PROBE_SIZES",
    "CheckReport",
    "check_board",
    "check_decision_models",
    "check_request",
]
