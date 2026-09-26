"""Turns that crashed, kept for the operator and nobody else (OPS-21).

A turn that raises something the worker has no rule for — a constraint violation, a bug — used to
escape `run_forever` and take the whole worker process down with it. Docker restarted the process,
the job had already been acked, and the game sat "running" with nothing in its log until the stall
sweep requeued it forty-five minutes later. `c4550202` did that once per resume for a day, and the
only trace was a traceback scrolled out of `./chessmark logs` long before anybody looked.

So a crash is written here, and `./chessmark status` reads it back. **Never into `game_events`**:
the event log is what spectators see, and a stack trace is not something a reader of the game can
act on. The operator can, and this is where they look.

A capped Redis list rather than a table: nothing about a crash needs to outlive the fix for it, and
a status command that needs a migration to learn about failures is one more thing that can fail.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

KEY = "chessmark:failures"

#: Enough to see a pattern, few enough that the list never matters to Redis.
KEEP = 200

#: A message is kept to this many characters. SQLAlchemy appends the statement and every bound
#: parameter to its errors — including whole tool results — and the head is the part that names it.
MESSAGE_LIMIT = 500

#: The wrapper SQLAlchemy and asyncpg put in front of a database error:
#: `(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg...'>: `. Seventy
#: characters of it filled the status table's column, and the constraint's name — the one thing
#: that says which bug this is — was cut off after it.
_WRAPPER = re.compile(r"^(\([^)]*\)\s*)?(<class '[^']*'>:\s*)?")


@dataclass(frozen=True, slots=True)
class Failure:
    at: dt.datetime
    game_id: str
    ply: int
    error: str
    message: str


class FailureLog:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def record(self, game_id: uuid.UUID, ply: int, error: BaseException) -> None:
        entry = {
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "game_id": str(game_id),
            "ply": ply,
            "error": type(error).__name__,
            "message": _WRAPPER.sub("", str(error))[:MESSAGE_LIMIT],
        }
        await self._redis.lpush(KEY, json.dumps(entry))
        await self._redis.ltrim(KEY, 0, KEEP - 1)

    async def recent(self, since: dt.datetime) -> list[Failure]:
        """Newest first, back to `since`."""
        out: list[Failure] = []
        for raw in await self._redis.lrange(KEY, 0, KEEP - 1):
            entry = json.loads(raw)
            at = dt.datetime.fromisoformat(entry["at"])
            if at < since:
                break
            out.append(
                Failure(
                    at=at,
                    game_id=entry["game_id"],
                    ply=int(entry["ply"]),
                    error=entry["error"],
                    message=entry["message"],
                )
            )
        return out


__all__ = ["KEEP", "KEY", "Failure", "FailureLog"]
