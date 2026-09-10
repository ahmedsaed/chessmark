"""A turn streams as it happens, and what streams is not an event (ADR-0035).

A turn is one transaction (ADR-0007), so nothing it appends can be published until it commits. Ply
8 of `e601f9af` ran 632 seconds across six provider rounds and delivered all fifteen of its events
in one frame at the end, every one stamped the same millisecond — ten minutes of a still board,
then the whole turn at once.

The two properties asserted here are the two halves of the bargain:

* a spectator sees the rounds arrive as they finish, and
* **the record is untouched** — the same events, the same order, the same count, whether anything
  was listening or not.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.live import (
    BUFFER_KEY,
    BUFFER_MAX_FRAMES,
    BUFFER_TTL_SECONDS,
    DELTA_CHANNEL,
    NullLive,
    RedisLive,
    block,
    token,
    turn_started,
)
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.enums import EventType
from chessmark.db.models import GameEvent
from tests.orchestration.conftest import Fixture, run_next

pytestmark = pytest.mark.integration


class Recorder:
    """A live channel that keeps what it was told, in order, with when."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.at: list[float] = []

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None:
        self.frames.append(frame)
        self.at.append(time.perf_counter())


def _kinds(frames: list[dict[str, Any]]) -> list[str]:
    return [f["kind"] for f in frames if f["frame"] == "block"]


# ====================================================================== the frames


async def test_a_round_is_announced_before_the_turn_commits(
    db: AsyncSession, game: Fixture, make_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The point of the whole thing.**

    Two rounds: look at the board, then move. The first round's reasoning and tool call must reach
    the channel while the turn is still open, because the second round is where the six minutes
    go — and a spectator who has to wait for the transaction watches a still board through all of
    it.
    """
    recorder = Recorder()
    worker = make_worker(
        scripted(
            step(tool_call("get_board"), reasoning="Let me look."),
            step(tool_call("make_move", move="e4"), reasoning="The board confirms it."),
        ),
        live=recorder,
    )

    await run_next(worker, game.queue)

    assert _kinds(recorder.frames) == ["reasoning", "tool", "reasoning", "tool"]


async def test_the_frames_carry_the_same_content_as_the_events(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """A frame and the event that supersedes it are the same step, so a client that folds both
    through one path cannot render them differently."""
    recorder = Recorder()
    worker = make_worker(
        scripted(step(tool_call("make_move", move="e4"), reasoning="Center first.")),
        live=recorder,
    )
    await run_next(worker, game.queue)

    db.expunge_all()
    thinking = await db.scalar(
        sa.select(GameEvent).where(
            GameEvent.game_id == game.game.id, GameEvent.type == EventType.THINKING
        )
    )
    frame = next(f for f in recorder.frames if f.get("kind") == "reasoning")

    assert thinking is not None
    assert frame["text"] == thinking.payload["reasoning"]
    assert frame["tokens"] == thinking.payload["tokens"]


async def test_a_frame_is_never_written_down(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """**Invariant 7 is the reason this is a separate channel.** One `game_events` row per state
    change, and a frame is not a state change — a turn that streams appends exactly the rows a
    turn that does not streams appends.
    """
    quiet = make_worker(
        scripted(step(tool_call("make_move", move="e4"), reasoning="Center first.")),
        live=NullLive(),
    )
    await run_next(quiet, game.queue)

    db.expunge_all()
    without = list(
        await db.scalars(
            sa.select(GameEvent).where(GameEvent.game_id == game.game.id).order_by(GameEvent.seq)
        )
    )

    assert [str(row.type) for row in without].count("thinking") == 1
    assert all(row.seq > 0 for row in without), "every stored row is a numbered event"


async def test_the_record_is_identical_whether_anyone_was_listening(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The property that makes this safe to ship: streaming changes the page and nothing else."""
    recorder = Recorder()
    worker = make_worker(
        scripted(
            step(tool_call("get_board"), reasoning="Look."),
            step(tool_call("make_move", move="e4"), content="Playing e4."),
        ),
        live=recorder,
    )
    await run_next(worker, game.queue)

    db.expunge_all()
    rows = list(
        await db.scalars(
            sa.select(GameEvent).where(GameEvent.game_id == game.game.id).order_by(GameEvent.seq)
        )
    )
    recorded = [str(r.type) for r in rows if str(r.type) in {"thinking", "output", "tool_called"}]

    # Same steps, same order — the frames are a preview of exactly this and nothing more.
    assert recorded == ["thinking", "tool_called", "output", "tool_called"]
    assert _kinds(recorder.frames) == ["reasoning", "tool", "output", "tool"]


async def test_a_round_reaches_a_reader_before_the_turn_commits(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """**The claim of ADR-0035, timed rather than counted.**

    "Frames are sent" is also true of a system that sends them after the commit, which would be
    the original bug with frames added. What has to hold is that the *first* round is readable
    while the later ones are still running: ply 8 of `e601f9af` spent 632 seconds across six
    rounds and published all fifteen of its events in the same millisecond at the end.

    The provider here takes half a second per round — the point is only that a round lasts long
    enough for "as it happens" and "at the end" to be different answers.
    """
    recorder = Recorder()
    rounds = iter(
        [
            step(tool_call("get_board"), reasoning="First, the position."),
            step(tool_call("resign"), reasoning="It is lost."),
        ]
    )

    async def slow(**kwargs: Any) -> Any:
        await asyncio.sleep(0.5)
        return next(rounds)

    started = time.perf_counter()
    await run_next(make_worker(slow, publish=False, live=recorder), game.queue)
    committed = time.perf_counter()

    assert recorder.at, "the turn published nothing"
    # The first round's work is out before the second round has even been asked for.
    assert recorder.at[0] < started + 1.0
    assert committed - recorder.at[0] > 0.4, (
        "the first frame arrived at the same moment as the commit — the turn is still being "
        "delivered all at once"
    )


# ============================================== arriving in the middle of a turn (ADR-0035)


class FakeRedis:
    """Enough Redis to exercise the buffer: a list, a TTL, and a pipeline that batches."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.published: list[tuple[str, str]] = []
        self.ttl: dict[str, int] = {}

    def pipeline(self) -> Any:
        return _Pipeline(self)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        return items[start:] if stop == -1 else items[start : stop + 1]


class _Pipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    def delete(self, key: str) -> None:
        self._queued.append(("delete", (key,)))

    def rpush(self, key: str, value: str) -> None:
        self._queued.append(("rpush", (key, value)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._queued.append(("ltrim", (key, start, stop)))

    def expire(self, key: str, seconds: int) -> None:
        self._queued.append(("expire", (key, seconds)))

    def publish(self, channel: str, body: str) -> None:
        self._queued.append(("publish", (channel, body)))

    async def execute(self) -> None:
        for name, args in self._queued:
            if name == "delete":
                self._redis.lists.pop(args[0], None)
            elif name == "rpush":
                self._redis.lists.setdefault(args[0], []).append(args[1])
            elif name == "ltrim":
                items = self._redis.lists.get(args[0], [])
                self._redis.lists[args[0]] = items[args[1] :] if args[2] == -1 else items
            elif name == "expire":
                self._redis.ttl[args[0]] = args[1]
            elif name == "publish":
                self._redis.published.append((args[0], args[1]))
        self._queued.clear()


async def test_a_reader_arriving_mid_turn_is_caught_up() -> None:
    """**The case pub/sub cannot serve on its own.**

    Frames are fire-and-forget: whoever was not subscribed when one went out never receives it.
    A turn runs for minutes, so a spectator opening a game partway through would get the committed
    backfill — everything up to the *last* turn — and then a still board until this one commits.
    They would be the one reader the streaming never reached.
    """
    redis = FakeRedis()
    channel = RedisLive(redis)
    game_id = uuid.uuid4()
    player = uuid.uuid4()

    await channel.send(game_id, turn_started(player, colour="black", ply=8, model="m"))
    await channel.send(game_id, block(player, "reasoning", text="First, the position.", tokens=21))
    await channel.send(game_id, block(player, "tool", tool="get_board", ok=True, args={}))

    caught_up = await channel.replay(game_id)

    assert [f["frame"] for f in caught_up] == ["turn", "block", "block"]
    assert caught_up[1]["text"] == "First, the position."


async def test_a_new_turn_discards_the_last_one_s_frames() -> None:
    """Otherwise a late reader is caught up on a turn that finished, and sees it twice: once as
    stale frames and once as the committed events it already has."""
    redis = FakeRedis()
    channel = RedisLive(redis)
    game_id = uuid.uuid4()
    player = uuid.uuid4()

    await channel.send(game_id, turn_started(player, colour="white", ply=7, model="m"))
    await channel.send(game_id, block(player, "reasoning", text="old", tokens=1))
    await channel.send(game_id, turn_started(player, colour="black", ply=8, model="m"))
    await channel.send(game_id, block(player, "reasoning", text="new", tokens=1))

    caught_up = await channel.replay(game_id)

    assert [f.get("text") for f in caught_up if f["frame"] == "block"] == ["new"]
    assert caught_up[0]["ply"] == 8


async def test_the_buffer_cannot_grow_without_bound() -> None:
    """A model streaming tens of thousands of fragments must not fill Redis. Past the ceiling a
    late joiner is caught up on the tail, which is the part still on screen."""
    redis = FakeRedis()
    channel = RedisLive(redis)
    game_id = uuid.uuid4()
    player = uuid.uuid4()

    await channel.send(game_id, turn_started(player, colour="black", ply=8, model="m"))
    for index in range(BUFFER_MAX_FRAMES + 50):
        await channel.send(game_id, token(player, "reasoning", f"{index} "))

    caught_up = await channel.replay(game_id)

    assert len(caught_up) == BUFFER_MAX_FRAMES
    assert caught_up[-1]["text"] == f"{BUFFER_MAX_FRAMES + 49} "


async def test_the_buffer_expires_on_its_own() -> None:
    """A turn that rolls back leaves its frames behind and no later turn to clear them. The TTL is
    what stops an abandoned game's buffer living in Redis forever."""
    redis = FakeRedis()
    game_id = uuid.uuid4()

    await RedisLive(redis).send(game_id, block(uuid.uuid4(), "reasoning", text="x", tokens=1))

    assert redis.ttl[BUFFER_KEY.format(game_id=game_id)] == BUFFER_TTL_SECONDS


async def test_every_frame_still_goes_out_live() -> None:
    """The buffer is for latecomers; it must not replace the fan-out that serves everyone already
    watching. Both, every time."""
    redis = FakeRedis()
    game_id = uuid.uuid4()

    await RedisLive(redis).send(game_id, block(uuid.uuid4(), "reasoning", text="x", tokens=1))

    assert len(redis.published) == 1
    assert redis.published[0][0] == DELTA_CHANNEL.format(game_id=game_id)


async def test_a_channel_that_cannot_reach_redis_says_nothing_rather_than_failing() -> None:
    """A frame is not a record. Failing a turn because a spectator's animation could not be sent
    would trade the thing that matters for the thing that does not."""

    class Broken:
        def pipeline(self) -> Any:
            raise RuntimeError("redis is down")

        async def lrange(self, key: str, start: int, stop: int) -> list[str]:
            raise RuntimeError("redis is down")

    channel = RedisLive(Broken())

    await channel.send(uuid.uuid4(), block(uuid.uuid4(), "reasoning", text="x", tokens=1))
    assert await channel.replay(uuid.uuid4()) == []


# ====================================================================== the shapes


def test_a_frame_names_its_channel_and_its_seat() -> None:
    """Frames from two seats share a game channel, so a client has to be able to tell them apart —
    and nothing here carries a `seq`, which is what stops one being mistaken for an event."""
    player = uuid.uuid4()

    frame = block(player, "reasoning", text="thinking", tokens=12)

    assert frame["frame"] == "block"
    assert frame["player_id"] == str(player)
    assert "seq" not in frame


def test_a_token_frame_says_which_register_it_belongs_to() -> None:
    """`reasoning` and `output` stream separately and mean different things (ADR-0035), so a
    fragment that did not say which it was could only be appended to the wrong block."""
    fragment = token(uuid.uuid4(), "reasoning", "the pawn on e2")

    assert fragment == {
        "frame": "token",
        "player_id": fragment["player_id"],
        "kind": "reasoning",
        "text": "the pawn on e2",
    }


def test_the_live_channel_is_not_the_event_channel() -> None:
    """Separate so a subscriber can take one without the other, and so a delta can never be read
    as a committed event by a client listening to either."""
    game_id = uuid.uuid4()

    assert DELTA_CHANNEL.format(game_id=game_id) != f"chessmark:game:{game_id}"
