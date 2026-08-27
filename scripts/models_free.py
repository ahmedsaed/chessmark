"""Which free models are currently served, tool-capable, and playable.

Exists for one recurring nuisance: a free slug is withdrawn without notice, and anything pinned to
it fails in a way that looks like the model played badly rather than like the model is gone. The
live-provider test (`tests/agents/test_live_game.py`) pins one, and so does anyone benchmarking by
hand.

Reads the registry rather than OpenRouter, so it answers "what would a game actually get" — a
model with no active tool-capable endpoint cannot be played whatever the catalogue says.
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa

from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.db.session import session_scope


async def main() -> None:
    async with session_scope() as session:
        rows = (
            await session.execute(
                sa.select(
                    ModelRegistry.openrouter_id,
                    ModelRegistry.context_length,
                    sa.func.count(ModelEndpoint.id).label("endpoints"),
                    sa.func.max(ModelEndpoint.uptime_1d).label("best_uptime"),
                )
                .join(ModelEndpoint, ModelEndpoint.model_id == ModelRegistry.id)
                # The same predicate `GET /models` applies, so this cannot advertise a model the
                # picker would refuse: enabled, tool-capable, and holding at least one active
                # tool-capable endpoint. A model with no such endpoint has no contestant and
                # cannot be played, whatever the catalogue says about it.
                .where(
                    ModelRegistry.is_free.is_(True),
                    ModelRegistry.enabled.is_(True),
                    ModelRegistry.supports_tools.is_(True),
                    ModelEndpoint.is_active.is_(True),
                    ModelEndpoint.supports_tools.is_(True),
                )
                .group_by(ModelRegistry.openrouter_id, ModelRegistry.context_length)
                .order_by(sa.desc("best_uptime"))
            )
        ).all()

    if not rows:
        print("no free playable models in the registry — run `make refresh-catalogue` first")
        return

    print(f"{'model':<52} {'ctx':>9} {'endpoints':>9} {'uptime':>7}")
    for slug, context, endpoints, uptime in rows:
        uptime_text = f"{uptime:.1f}%" if uptime is not None else "—"
        print(f"{slug:<52} {context or 0:>9,} {endpoints:>9} {uptime_text:>7}")


if __name__ == "__main__":
    asyncio.run(main())
