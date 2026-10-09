"""What a seat's reasoning level puts on the wire (ADR-0067).

The level recorded on the seat is only half of it; a record that says `high` beside requests that
never asked for it is the failure this ADR exists to end. So: the level is sent on every call of
the turn, it is read from the seat rather than the catalogue's current default, and a seat with no
level sends exactly what it always did.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.models import LlmCall, ModelRegistry, Player
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

SLUG = "test/effortful"


async def _seat_at(db: AsyncSession, table: Table, effort: str | None, *, default: str) -> None:
    model = ModelRegistry(
        openrouter_id=SLUG,
        display_name=SLUG,
        provider="test",
        context_length=128_000,
        prompt_usd_per_token=Decimal("0.0000001"),
        completion_usd_per_token=Decimal("0.0000004"),
        reasoning_levels=["none", "low", "medium", "high"],
        default_reasoning=default,
    )
    db.add(model)
    await db.flush()
    sampling: dict[str, Any] = {"model": SLUG}
    if effort is not None:
        sampling["effort"] = effort
    await db.execute(
        sa.update(Player)
        .where(Player.id == table.white.id)
        .values(model_id=model.id, sampling=sampling)
    )
    await db.commit()
    await db.refresh(table.white)


async def _sent(db: AsyncSession, table: Table) -> list[dict[str, Any]]:
    calls = await db.scalars(
        sa.select(LlmCall).where(LlmCall.game_id == table.game.id).order_by(LlmCall.sequence)
    )
    return [call.request.get("extra_body", {}) for call in calls]


async def test_the_seats_level_is_sent_on_every_call(db: AsyncSession, table: Table) -> None:
    await _seat_at(db, table, "high", default="medium")

    await play_turn(
        db,
        table,
        scripted(step(tool_call("get_legal_moves")), step(tool_call("make_move", move="e4"))),
        model=SLUG,
    )

    sent = await _sent(db, table)
    assert len(sent) >= 2
    assert all(body.get("reasoning") == {"enabled": True, "effort": "high"} for body in sent)


async def test_the_level_comes_from_the_seat_not_todays_default(
    db: AsyncSession, table: Table
) -> None:
    """A default that moved after the game was created must not change the game in progress."""
    await _seat_at(db, table, "low", default="high")

    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))), model=SLUG)

    assert (await _sent(db, table))[0]["reasoning"] == {"enabled": True, "effort": "low"}


async def test_none_switches_reasoning_off(db: AsyncSession, table: Table) -> None:
    await _seat_at(db, table, "none", default="medium")

    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))), model=SLUG)

    assert (await _sent(db, table))[0]["reasoning"] == {"enabled": False}


async def test_a_seat_with_no_level_sends_no_reasoning_field(
    db: AsyncSession, table: Table
) -> None:
    """Every game from before ADR-0067 is one of these, and its requests must not change."""
    await _seat_at(db, table, None, default="medium")

    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))), model=SLUG)

    assert "reasoning" not in (await _sent(db, table))[0]
