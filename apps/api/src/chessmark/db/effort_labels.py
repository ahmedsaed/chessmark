"""Labelling the seats that played before a reasoning level was recorded (ADR-0067).

No game before ADR-0067 sent a reasoning level, and none recorded one. Those seats played at their
provider's default, and a contestant is now `(model, quantization, effort)`, so each needs a level
for its games to be rated beside the ones that follow.

**From what the seat did, read through what the catalogue says.** A seat whose calls produced
reasoning tokens was reasoning, so it gets the effort its model uses when reasoning is on. A seat
whose calls produced none was not, so it gets `none` where the model offers it. The tokens are
evidence that was recorded at the time. The level names come from the catalogue as it is today,
and that is the part that can be wrong: a provider whose default moved since will have mislabelled
seats. Each label is marked `effort_inferred` so the methodology page can say so.

Runs inside the catalogue refresh, after the registry sync that gives models their levels, so it
needs no operator step at deploy. It touches only seats with no level, and every seat created from
ADR-0067 on has one, so after its first pass it has nothing left to do except seats seated in the
minutes between the migration and that pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.effort import AUTO, NONE
from chessmark.db.enums import ModelRuntime, PlayerKind
from chessmark.db.models import LeaderboardSnapshot, LlmCall, ModelRegistry, Player, Turn


@dataclass(slots=True)
class LabelReport:
    labelled: int = 0
    by_level: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        if not self.labelled:
            return "no unlabelled seats"
        levels = ", ".join(f"{level} {n}" for level, n in sorted(self.by_level.items()))
        return f"{self.labelled} seats labelled ({levels})"


def inferred_level(
    *, reasoned: bool, offered: list[str], default: str, raw: dict[str, Any] | None
) -> str:
    """The level an unlabelled seat most likely played at.

    A seat that reasoned played with reasoning on: at the model's default if that is on, and
    otherwise at the effort the catalogue says it uses when switched on (`default_effort`), which is
    the case of an optional model whose "off by default" was read from a missing flag. A seat that
    never reasoned played at `none` if the model offers it. An always-on model that produced no
    reasoning tokens is labelled at its default anyway: it cannot be asked not to reason, so the
    zero says the provider did not report them, not that the model did not think.
    """
    if not reasoned:
        return NONE if NONE in offered else default
    if default != NONE:
        return default
    named = (raw or {}).get("default_effort")
    if named in offered:
        return str(named)
    if AUTO in offered:
        return AUTO
    on = [level for level in offered if level != NONE]
    return on[0] if on else default


async def label_unlabelled_seats(session: AsyncSession) -> LabelReport:
    """Give every chat seat without a reasoning level the one it most likely played at.

    Only seats whose model the catalogue now describes; the rest wait for a later pass. One read
    for every candidate, with whether it ever reasoned folded in, rather than a query per seat.
    """
    reasoned = (
        sa.select(sa.literal(1))
        .select_from(LlmCall)
        .join(Turn, Turn.id == LlmCall.turn_id)
        .where(Turn.player_id == Player.id, LlmCall.reasoning_tokens > 0)
        .exists()
    )
    rows = (
        await session.execute(
            sa.select(
                Player,
                ModelRegistry.reasoning_levels,
                ModelRegistry.default_reasoning,
                ModelRegistry.reasoning,
                reasoned.label("reasoned"),
            )
            .join(ModelRegistry, ModelRegistry.id == Player.model_id)
            .where(
                Player.kind == PlayerKind.MODEL,
                # A decision seat has no reasoning parameter and so no level (ADR-0049).
                Player.runtime != ModelRuntime.DECISION,
                ModelRegistry.reasoning_levels.is_not(None),
                ModelRegistry.default_reasoning.is_not(None),
                sa.not_(Player.sampling.has_key("effort")),
            )
        )
    ).all()

    report = LabelReport()
    for player, offered, default, raw, did_reason in rows:
        level = inferred_level(
            reasoned=bool(did_reason), offered=list(offered), default=str(default), raw=raw
        )
        # A new dict, not a mutation: JSONB columns are not change-tracked in place.
        player.sampling = {**(player.sampling or {}), "effort": level, "effort_inferred": True}
        report.labelled += 1
        report.by_level[level] = report.by_level.get(level, 0) + 1

    if report.labelled:
        # The stored runs are keyed by contestant, and these seats just changed contestant. Their
        # fingerprint is over the games, which did not change, so a run would go on serving the old
        # keys. Every row is a cache the next read rebuilds (ADR-0061).
        await session.execute(sa.delete(LeaderboardSnapshot))
    await session.flush()
    return report
