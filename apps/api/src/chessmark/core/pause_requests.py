"""A person asking to pause a game they pay for, held until the worker can act on it (ADR-0052).

**Why a request and not a write.** A turn holds its game's row lock for the whole of the turn
(ADR-0022), and a reasoning model's turn can run for minutes. A pause written straight to the game
would sit behind that lock for as long, with the person's click hanging on it. So the click records
the request here, returns at once, and the worker honours it **before the next turn** — the same
point it checks a payer's credit. The turn already in progress finishes, and is charged.

Kept in Redis with a day's expiry because it is only ever a request in transit: it exists from the
click until the next turn starts, which is seconds when a turn is not running and minutes when one
is. Losing it — a Redis flush in that window — means the game plays on, spending credit its owner
still controls, and the button is there to press again. Nothing here is the record: the pause
itself is a `game_paused` event, appended by the worker like every other state change
(invariant 7).
"""

from __future__ import annotations

import uuid
from typing import Any

#: Long enough to outlive any turn, short enough that a request for a game that ended meanwhile
#: does not linger.
TTL_SECONDS = 24 * 60 * 60

_KEY = "chessmark:pause-requested:{game_id}"


class PauseRequests:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def request(self, game_id: uuid.UUID) -> None:
        await self._redis.set(_KEY.format(game_id=game_id), "1", ex=TTL_SECONDS)

    async def pending(self, game_id: uuid.UUID) -> bool:
        return bool(await self._redis.exists(_KEY.format(game_id=game_id)))

    async def clear(self, game_id: uuid.UUID) -> bool:
        """Withdraw the request. True when there was one."""
        return bool(await self._redis.delete(_KEY.format(game_id=game_id)))


__all__ = ["PauseRequests"]
