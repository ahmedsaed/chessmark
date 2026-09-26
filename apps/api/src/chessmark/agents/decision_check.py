"""Whether a decision model can answer what we ask, found out once, before it is ever paired.

**Nothing in the catalogue says which question types a decision model accepts** (ADR-0051).
Span-01 and Jev list identical metadata — the same modality, empty `supported_parameters` — and yet
Span refuses anything but yes-or-no questions over a conversation:

    "Respan only accepts noul questions whose instructions and criteria are plain strings"

Paired blind, such a model is refused at ply 0 of every game it is given, and each refusal is an
abandoned pairing in the record. So a new model is asked **one** tiny request in exactly the shape
a real turn sends — a two-move position, the move `choice`, the action `choice` — and the answer is
written on its registry row. A refusal is not billed; an answer costs a few hundred input tokens.

**Once per model per `DECISION_VERSION`**, and never on a routine refresh: a new version may ask a
question a host treats differently, and nothing else can change what it accepts. A failure that is
about the moment rather than the model — a rate limit, an outage, an empty account — records
nothing, and the next refresh asks again.

This is a capability check, not calibration. `d2` has no thresholds to tune (ADR-0051), so a model
that can answer the question is ready to play.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import chess
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import (
    ACTION_QUESTION,
    DECISION_VERSION,
    MOVE_QUESTION,
    build_request,
)
from chessmark.agents.decisions import DecisionGateway, MalformedDecisionError
from chessmark.agents.types import LlmError
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import ModelRegistry

log = logging.getLogger(__name__)

#: The position the check asks about. Two plies in, so the request carries history and a last move,
#: and every field a real turn sends is present in it.
CHECK_MOVES = ("e4", "e5")

#: How much of a host's refusal is kept. The message is the useful part — it says what the host
#: does accept — and a whole error body would be noise on a registry row.
REFUSAL_LENGTH = 500


@dataclass(slots=True)
class CheckReport:
    answered: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: Could not be asked this time — rate-limited, unreachable — and will be asked again.
    deferred: list[str] = field(default_factory=list)

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


def check_request(model: str) -> tuple[dict[str, object], set[str], set[str]]:
    """The request the check sends, and the move and action options it must answer from."""
    board = chess.Board()
    for san in CHECK_MOVES:
        board.push_san(san)
    request = build_request(board)
    return request.body(model=model), set(request.moves), set(request.actions)


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
        body, moves, actions = check_request(model.openrouter_id)
        try:
            decision = await gateway.decide(body)
            decision.choice(MOVE_QUESTION, moves)
            decision.choice(ACTION_QUESTION, actions)
        except LlmError as error:
            if not error.request_rejected:
                # About the moment, not the model: nothing is recorded, the next refresh asks again.
                log.warning("decision check deferred for %s: %s", model.openrouter_id, error)
                report.deferred.append(model.openrouter_id)
                continue
            refusal = _host_message(error.message)
        except MalformedDecisionError as error:
            # It answered, but not with an answer to our question — as unusable in a game as a
            # refusal, and for the same reason.
            refusal = f"answered with something unusable: {error}"
        else:
            model.decisions_checked = DECISION_VERSION
            model.decisions_refusal = None
            report.answered.append(model.openrouter_id)
            continue

        model.decisions_checked = DECISION_VERSION
        model.decisions_refusal = refusal[:REFUSAL_LENGTH]
        log.warning("%s cannot answer our decision requests: %s", model.openrouter_id, refusal)
        report.refused.append(model.openrouter_id)

    await session.flush()
    return report


__all__ = ["CHECK_MOVES", "CheckReport", "check_decision_models", "check_request"]
