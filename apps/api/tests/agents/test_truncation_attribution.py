"""A ceiling is never a strike against a player (invariant 11, ADR-0019, ADR-0021, ADR-0024).

`finish_reason: "length"` says an answer was cut off and nothing about who cut it. Two cases, and
they are distinguishable from the response rather than arguable: we know what `max_tokens` we asked
for, and the provider reports what it generated. **Neither is a finding**, and they differ only in
how quickly the turn gives up.

* Our ceiling bound it → fail immediately. There is nothing for a nudge to fix.
* The provider's own ceiling bound it → retry up to `MAX_TRUNCATIONS`, telling the model it was cut
  off, because a model often recovers on the second attempt. Then fail the turn.

Neither case is hypothetical. A miscalculated window asked an endpoint for **one** output token,
every reply came back truncated, and four of them ended a real game `truncated`, `1-0`, against
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at ply 5 (ADR-0021). And `poolside/laguna-s-2.1`
lost a game holding rook and two bishops against a lone pawn because Poolside stops at 32,768
output tokens while we asked for 64,000 — a number no response from that endpoint could reach, so
the check above could never fire (ADR-0024).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import MAX_TRUNCATIONS, TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.game import Termination
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


def truncated(*, completion_tokens: int) -> dict[str, object]:
    """A response cut off at `length`, having generated exactly this much."""
    return step(finish_reason="length", completion_tokens=completion_tokens)


async def _register(db: AsyncSession, *, slug: str, context: int) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": slug, "display_name": slug, "context_length": context}]
    )
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
    )
    db.add(
        ModelEndpoint(
            model_id=model_id,
            provider_name="TestProvider",
            context_length=context,
            supports_tools=True,
            is_active=True,
        )
    )
    await db.commit()


async def test_our_own_ceiling_fails_the_turn_and_forfeits_nobody(
    db: AsyncSession, table: Table
) -> None:
    """The exact shape of the real forfeit: every response stops at the number we asked for."""
    limits = TurnLimits(max_completion_tokens=4_000)

    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=4_000), repeat_last=True),
        limits=limits,
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "a harness bound is never a finding about a player"
    assert "max_tokens" in (result.error or ""), result.error


async def test_it_does_not_spend_the_strike_budget(db: AsyncSession, table: Table) -> None:
    """It fails on the first one. Counting four of them is how the forfeit was reached."""
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return truncated(completion_tokens=4_000)

    await play_turn(db, table, counting, limits=TurnLimits(max_completion_tokens=4_000))

    assert calls == 1, f"our own ceiling is not retried into a forfeit, got {calls} calls"


async def test_a_providers_own_ceiling_fails_the_turn_too(db: AsyncSession, table: Table) -> None:
    """Stopping *short* of what we allowed is the endpoint's own limit — and that is a fact about
    the host, not the weights (ADR-0024).

    It used to forfeit, on the reasoning that the provider's ceiling was a generous natural budget
    a model ought to finish inside. That is the same argument `TIMEOUT` lost: the same weights on an
    endpoint with a larger ceiling are not cut off, so the verdict was decided by routing.
    """
    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=900), repeat_last=True),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "the endpoint's ceiling is not a finding about a player either"


async def test_the_retries_are_spent_before_the_turn_fails(db: AsyncSession, table: Table) -> None:
    """The nudge is worth keeping even though the ending changed: a model told it was cut off often
    acts on the next attempt, and failing the turn immediately would throw that away."""
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return truncated(completion_tokens=900)

    await play_turn(db, table, counting, limits=TurnLimits(max_completion_tokens=64_000))

    assert calls == MAX_TRUNCATIONS + 1, f"expected the strike budget to be spent, got {calls}"


async def test_a_provider_truncation_is_retried_first(db: AsyncSession, table: Table) -> None:
    """A model cut off mid-reasoning is told so and gets to try again — it did not refuse."""
    result = await play_turn(
        db,
        table,
        scripted(
            truncated(completion_tokens=900),
            step(tool_call("make_move", move="e4")),
        ),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.move is not None and result.move.move.san == "e4"


async def test_an_unreported_generation_count_is_still_not_a_finding(
    db: AsyncSession, table: Table
) -> None:
    """Unattributable — an endpoint that reports no usage — takes the retry path and then fails the
    turn. It cannot be blamed on the model, because nothing here says the model did anything."""
    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=0), repeat_last=True),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None
    assert MAX_TRUNCATIONS >= 1


async def test_a_truncated_ending_is_reopenable_and_unrated(db: AsyncSession, table: Table) -> None:
    """The classification that follows from all of the above, asserted where a reader of this file
    will look for it. `tests/bench/test_classification.py` holds the four sets to each other."""
    from chessmark.bench.ratable import HARNESS_TERMINATIONS, RATED_TERMINATIONS
    from chessmark.game import FORFEIT_TERMINATIONS, RESUMABLE_TERMINATIONS

    assert Termination.TRUNCATED in RESUMABLE_TERMINATIONS
    assert Termination.TRUNCATED in HARNESS_TERMINATIONS
    assert Termination.TRUNCATED not in FORFEIT_TERMINATIONS
    assert Termination.TRUNCATED not in RATED_TERMINATIONS
