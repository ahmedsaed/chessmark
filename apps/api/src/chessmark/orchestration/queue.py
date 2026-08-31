"""The turn queue.

A job is **"advance game G from ply N"**, never "play game G" (ADR-0007). That single choice is
what makes a worker crash survivable: the job carries the ply it expects, so a redelivery after a
crash either reruns an uncommitted turn or harmlessly no-ops.

Built on Redis Streams with a consumer group rather than a plain list, because the two properties
we need are exactly what streams provide:

* **At-least-once with acknowledgement.** A job stays in the group's pending list until the worker
  acks it, which it does only *after* the turn commits. Kill the worker mid-turn and the job is
  still there.
* **Reclaim.** `XAUTOCLAIM` hands a dead worker's in-flight jobs to a live one after an idle
  timeout, with no external bookkeeping.

A plain `LPUSH`/`BRPOP` list would lose any job a worker held when it died.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

DEFAULT_STREAM = "chessmark:turns"
DEFAULT_GROUP = "workers"

#: Bounds the stream so a long-running deployment does not grow without limit. Approximate
#: trimming is much cheaper than exact and the excess is irrelevant.
DEFAULT_MAXLEN = 100_000

#: How long a delivery may sit unacked before another worker may claim it. Comfortably longer than
#: a slow turn (a reasoning model can legitimately take minutes), so a working worker is never
#: robbed of a job it is still executing.
DEFAULT_MIN_IDLE_MS = 15 * 60 * 1000

#: How long a consumer name may go untouched before it is forgotten.
#:
#: Generous on purpose. A live worker blocks on `XREADGROUP` for two seconds and comes straight
#: back, so ten minutes is three hundred poll intervals — nothing running can reach it. The cost of
#: being wrong is a name deleted and immediately recreated on the next read, which is harmless;
#: the cost of being *aggressive* would be deleting a name while its process still believed it
#: owned deliveries.
DEFAULT_CONSUMER_TTL_MS = 10 * 60 * 1000


@dataclass(frozen=True, slots=True)
class AdvanceTurn:
    """Advance `game_id` from `expected_ply`.

    `expected_ply` is the number of plies already committed when the job was created. The worker
    compares it to the game's real state and drops the job if they disagree — that comparison is
    the whole idempotency mechanism.
    """

    game_id: uuid.UUID
    expected_ply: int
    attempt: int = 1

    def to_fields(self) -> dict[str, str]:
        return {
            "game_id": str(self.game_id),
            "expected_ply": str(self.expected_ply),
            "attempt": str(self.attempt),
        }

    @classmethod
    def from_fields(cls, fields: dict[Any, Any]) -> AdvanceTurn:
        def get(key: str) -> str:
            value = fields.get(key) or fields.get(key.encode())
            return value.decode() if isinstance(value, bytes) else str(value)

        return cls(
            game_id=uuid.UUID(get("game_id")),
            expected_ply=int(get("expected_ply")),
            attempt=int(get("attempt") or 1),
        )

    def next_attempt(self) -> AdvanceTurn:
        return AdvanceTurn(
            game_id=self.game_id, expected_ply=self.expected_ply, attempt=self.attempt + 1
        )


@dataclass(frozen=True, slots=True)
class Delivery:
    """A job handed to a worker, plus the id it must ack."""

    message_id: str
    job: AdvanceTurn
    redelivered: bool = False


def _field(mapping: dict[Any, Any], key: str, default: Any) -> Any:
    """Read a field from a Redis reply whose keys may be `str` or `bytes`.

    Which one depends on how the client was built: the worker's `Redis.from_url` decodes nothing,
    the operator scripts pass `decode_responses=True`, and both call this code. A plain
    `mapping.get("name")` against a bytes-keyed reply returns the default and reports nothing —
    which would have made `reap_consumers` a silent no-op in production, found only because a test
    compared the names it expected against the ones it got.
    """
    if key in mapping:
        return mapping[key]
    return mapping.get(key.encode(), default)


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class TurnQueue:
    def __init__(
        self,
        redis: Redis[Any],
        *,
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
        maxlen: int = DEFAULT_MAXLEN,
    ) -> None:
        self.redis: Redis[Any] = redis
        self.stream = stream
        self.group = group
        self.maxlen = maxlen

    async def ensure_group(self) -> None:
        """Create the consumer group, tolerating the case where it already exists."""
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def enqueue(self, job: AdvanceTurn) -> str:
        message_id = await self.redis.xadd(
            self.stream, job.to_fields(), maxlen=self.maxlen, approximate=True
        )
        return _decode(message_id)

    async def consume(
        self, consumer: str, *, block_ms: int = 5000, count: int = 1
    ) -> list[Delivery]:
        """Take new jobs. Blocks up to `block_ms` waiting for one."""
        response = await self.redis.xreadgroup(
            self.group, consumer, {self.stream: ">"}, count=count, block=block_ms
        )
        return self._to_deliveries(response, redelivered=False)

    async def reclaim_stalled(
        self,
        consumer: str,
        *,
        min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
        count: int = 10,
    ) -> list[Delivery]:
        """Take over jobs a dead worker was holding (OPS-05).

        The uncommitted turn simply reruns; `expected_ply` still matches because the crash rolled
        the transaction back, so nothing was half-written.
        """
        _cursor, messages, _deleted = await self.redis.xautoclaim(
            self.stream, self.group, consumer, min_idle_time=min_idle_ms, count=count
        )
        return self._to_deliveries([(self.stream, messages)], redelivered=True)

    async def reap_consumers(self, *, idle_ms: int = DEFAULT_CONSUMER_TTL_MS) -> list[str]:
        """Forget consumer names whose process is gone. Returns the names removed.

        A worker's name is generated per **process**, so every restart abandons one, and Redis
        keeps a consumer in a group forever — only `XGROUP DELCONSUMER` removes it. Fifty-one names
        had accumulated across a few days of deploys, and `status` could not tell a reader which
        three of them were the running containers.

        **Never removes one holding a delivery.** `XGROUP DELCONSUMER` discards that consumer's
        pending entries, which would silently drop a turn; a worker mid-turn holds its job and is
        therefore protected by the same check, and a worker between turns has an idle time three
        hundred times under the threshold. Both safeguards are load-bearing and neither is enough
        alone: a *dead* worker's orphaned delivery must be left for `XAUTOCLAIM` to reclaim, not
        deleted along with the name.
        """
        removed: list[str] = []
        consumers = await self.redis.xinfo_consumers(self.stream, self.group)  # type: ignore[no-untyped-call]
        for consumer in consumers:
            name = _decode(_field(consumer, "name", b""))
            if int(_field(consumer, "pending", 0)) or int(_field(consumer, "idle", 0)) <= idle_ms:
                continue
            await self.redis.xgroup_delconsumer(self.stream, self.group, name)  # type: ignore[no-untyped-call]
            removed.append(name)
        return removed

    async def ack(self, message_id: str) -> None:
        """Acknowledge a job. Called only *after* the turn's transaction commits."""
        await self.redis.xack(self.stream, self.group, message_id)  # type: ignore[no-untyped-call]

    async def depth(self) -> int:
        return int(await self.redis.xlen(self.stream))

    async def pending_count(self) -> int:
        """Jobs delivered but not yet acked — in-flight plus abandoned."""
        summary = await self.redis.xpending(self.stream, self.group)  # type: ignore[no-untyped-call]
        if isinstance(summary, dict):
            return int(summary.get("pending", 0))
        return int(summary[0]) if summary else 0

    def _to_deliveries(self, response: Any, *, redelivered: bool) -> list[Delivery]:
        deliveries: list[Delivery] = []
        for _stream, messages in response or []:
            for message_id, fields in messages or []:
                if not fields:
                    continue
                deliveries.append(
                    Delivery(
                        message_id=_decode(message_id),
                        job=AdvanceTurn.from_fields(fields),
                        redelivered=redelivered,
                    )
                )
        return deliveries
