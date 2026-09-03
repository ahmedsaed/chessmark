"""The daily free-model allowance is a 429 that means something else entirely (OPS-20).

OpenRouter caps free models at 1,000 requests a day **across the account** (50 before ten credits
are bought), and reports it as `429 "Rate limit exceeded: free-models-per-day"`. On the wire it is
indistinguishable from a provider's shared pool being hot; in meaning it is the opposite of one.

Classified as an ordinary rate limit it rested a single model for sixty seconds, the matchmaker
paired the next entrant, that refused identically because the cap is on the *account*, and a
seventeen-model pool worked through its whole field one doomed request at a time — the shape the
402 halt was built for, arriving through a different code.

It is also the easiest halt to lift: `X-RateLimit-Reset` says exactly when the cap goes, so the
halt is written with that as its TTL and Redis lifts it. No probe, no midnight job.

The per-minute cap is deliberately *not* covered here. That one is a short wait and the cooldown
ladder is right for it.

**We no longer keep our own count** (ADR-0023). We did, and it was an over-count that declared the
allowance spent at 1,010 attempts while OpenRouter was still serving us — freezing every free game
for the rest of the UTC day. OpenRouter says when the allowance is gone; that is the only thing
that stops us now.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa

from chessmark.agents import llm
from chessmark.agents.scripted import plays
from chessmark.core.halt import SCOPE_FREE, SOURCE_FREE_TIER, SOURCE_OPERATOR, Halt
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import GameEvent
from chessmark.db.repositories import get_game
from chessmark.orchestration.reconciler import find_resumable
from chessmark.orchestration.worker import HALTED, next_utc_midnight
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration

DAILY_CAP = (
    "litellm.RateLimitError: OpenrouterException - "
    '{"error":{"message":"Rate limit exceeded: free-models-per-day. '
    'Add 10 credits to unlock 1000 free model requests per day","code":429}}'
)

SHARED_POOL = (
    "litellm.RateLimitError - "
    '{"error":{"message":"Provider returned error","code":429,'
    '"metadata":{"limit_source":"upstream_provider_shared_pool",'
    '"provider_name":"Google AI Studio"}}}'
)


class _Headers:
    def __init__(self, **values: str) -> None:
        self._values = values

    def get(self, name: str) -> str | None:
        return self._values.get(name) or self._values.get(name.lower())


class _Response:
    def __init__(self, headers: _Headers | None = None) -> None:
        self.headers = headers


class _RateLimitedError(Exception):
    status_code = 429

    def __init__(self, message: str, response: _Response | None = None) -> None:
        super().__init__(message)
        self.response = response


# ====================================================================== telling them apart


def test_the_daily_cap_is_told_from_a_hot_pool() -> None:
    """Same status code, opposite meanings: one is our account, the other is one provider."""
    assert llm.is_free_daily_cap(_RateLimitedError(DAILY_CAP))
    assert not llm.is_free_daily_cap(_RateLimitedError(SHARED_POOL))


def test_a_hot_pool_is_still_a_hot_pool() -> None:
    """The path that works must keep working: rest the provider, not the whole harness."""
    limit = llm.rate_limit_from(_RateLimitedError(SHARED_POOL))

    assert limit.is_upstream_pool
    assert not limit.free_daily_cap
    assert limit.provider == "Google AI Studio"


def test_the_reset_time_is_read_from_the_header() -> None:
    """`X-RateLimit-Reset` is exact where the ladder guesses, and the cap can be hours out."""
    at = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)
    error = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"X-RateLimit-Reset": str(int(at.timestamp() * 1000))}))
    )

    assert llm.resets_at(error) == at


def test_seconds_and_milliseconds_are_both_understood() -> None:
    """OpenRouter sends milliseconds; the header is defined in neither unit by anyone. `1e11`
    separates them with room to spare — that is 1973 in ms and the year 5138 in seconds."""
    at = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)

    as_seconds = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp()))}))
    )
    as_millis = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp() * 1000))}))
    )

    assert llm.resets_at(as_seconds) == at
    assert llm.resets_at(as_millis) == at


def test_a_missing_or_nonsense_header_says_nothing() -> None:
    """`None` means "it did not say", which the caller replaces with a conservative fallback —
    not with a number invented here."""
    assert llm.resets_at(_RateLimitedError(DAILY_CAP)) is None
    assert (
        llm.resets_at(
            _RateLimitedError(DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": "soon"})))
        )
        is None
    )
    assert (
        llm.resets_at(
            _RateLimitedError(DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": "0"})))
        )
        is None
    )


def test_the_fallback_is_the_next_utc_midnight() -> None:
    """Conservative on purpose: it can only be later than the true reset, so the worst case is
    waiting too long rather than resuming into a cap that has not lifted."""
    at = dt.datetime(2026, 8, 31, 23, 30, tzinfo=dt.UTC)

    assert next_utc_midnight(at) == dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.UTC)


# ====================================================================== what the worker does


async def test_the_daily_cap_halts_the_harness(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """One switch, not seventeen cooldowns discovered one request at a time."""

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == HALTED
    state = await worker.halt.state()
    assert state is not None
    assert state.source == SOURCE_FREE_TIER
    assert state.until is not None, "it lifts itself; nobody has to notice"
    assert state.scope == SCOPE_FREE, "a cap on the free distribution must not stop a paid model"


async def test_the_halt_expires_when_the_provider_said_it_would(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """The TTL *is* the expiry — nothing sweeps it, and there is no midnight job to fail."""
    at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(
            DAILY_CAP,
            _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp() * 1000))})),
        )

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    ttl = await redis.ttl("chessmark:halt")
    assert 3 * 3600 - 120 < ttl <= 3 * 3600, f"the key should expire with the cap, got ttl={ttl}"


async def test_the_daily_cap_never_ends_a_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """Invariant 11 again. Running out of somebody else's free allowance is not a chess result."""

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.PAUSED, "a stop it comes back from, not an ending"
    assert after.termination is None


async def test_a_hot_pool_still_pauses_one_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """The regression that would matter most: halting on every 429 would stop the whole site
    because one provider's free pool was busy for a minute."""

    async def hot(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(SHARED_POOL)

    worker = make_worker(hot)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused"
    assert await worker.halt.state() is None, "one busy pool must not stop the harness"


async def test_a_paid_model_plays_on_under_a_free_cap_halt(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope earning its place. A paid seat never drew on the free allowance, so stopping it
    would be an outage for a limit it is not subject to."""
    monkeypatch.setattr(
        "chessmark.orchestration.worker.model_for", lambda _player: "vendor/paid-model"
    )
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("the free-model allowance for the day is spent (429)", scope=SCOPE_FREE)

    handled = await worker.handle(game.first_job)

    assert handled.outcome != HALTED
    assert handled.result is not None, "a paid game keeps playing"


async def test_a_free_model_stops_under_a_free_cap_halt(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "chessmark.orchestration.worker.model_for", lambda _player: "vendor/model:free"
    )
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("the free-model allowance for the day is spent (429)", scope=SCOPE_FREE)

    assert (await worker.handle(game.first_job)).outcome == HALTED


# ====================================================================== saying so on the board


async def _pauses(db: Any, game_id: Any) -> list[Any]:
    rows = await db.scalars(
        sa.select(GameEvent).where(
            GameEvent.game_id == game_id, GameEvent.type == EventType.GAME_PAUSED
        )
    )
    return list(rows)


async def test_the_cap_pauses_the_game_rather_than_leaving_it_looking_live(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """The gap this closes. The halt is right about the *record* — no turn ran, nothing was spent
    — and it used to be invisible: the game stayed `RUNNING`, the header went on pulsing "live",
    and the board would not move again until UTC midnight. Up to a day of a game that looks alive.
    """

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == HALTED, "still a halt, not a hot pool — one switch, not a cooldown"

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.PAUSED
    assert after.pause_reason is not None
    assert "free-model allowance" in after.pause_reason, "why, not just that"


async def test_the_pause_is_in_the_event_log(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """Invariant 7. Live, reconnect and replay all read that one table, so a stop the log does not
    carry is a stop the page can never show — which is exactly what a halt used to be."""

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    db.expunge_all()
    events = await _pauses(db, game.game.id)

    assert len(events) == 1
    payload = events[0].payload
    assert payload["halt_source"] == SOURCE_FREE_TIER
    assert payload["halt_scope"] == SCOPE_FREE
    assert payload["resume_after"], "the cap says when it lifts, so the page can count down to it"
    assert payload["reason"]


async def test_the_game_comes_back_when_the_cap_does(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """`resume_after` is the halt's own expiry, so the reconciler puts the game back at the moment
    the allowance returns rather than on a schedule of its own."""
    at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(
            DAILY_CAP,
            _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp() * 1000))})),
        )

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.resume_after is not None
    assert abs((after.resume_after - at).total_seconds()) < 2

    assert game.game.id not in {g.id for g in await find_resumable(db)}, "not yet — the cap stands"
    due = await find_resumable(db, now=at + dt.timedelta(seconds=1))
    assert game.game.id in {g.id for g in due}


async def test_an_open_ended_halt_pauses_with_no_time_to_come_back(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """An operator halt lifts when somebody lifts it, and inventing a timestamp would be a
    countdown to a moment nothing has promised. `find_resumable` reads a missing `resume_after` as
    due, and the reconciler refuses to sweep while a halt stands — so it resumes on the first tick
    after the halt goes, which is the earliest honest answer."""
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("stopped by hand", source=SOURCE_OPERATOR)

    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.PAUSED
    assert after.resume_after is None
    assert game.game.id in {g.id for g in await find_resumable(db)}


async def test_a_second_job_does_not_append_a_second_notice(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """A redelivered job, or the reconciler requeueing a game it already paused, would otherwise
    publish another "paused" for a board that has not moved since the last one."""
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("stopped by hand", source=SOURCE_OPERATOR)

    await worker.handle(game.first_job)
    await worker.handle(game.first_job)

    db.expunge_all()
    assert len(await _pauses(db, game.game.id)) == 1


async def test_a_paid_game_is_not_paused_by_a_free_cap(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope again, on the new path. Pausing a paid seat for a limit it is not subject to would
    be an outage we invented."""
    monkeypatch.setattr(
        "chessmark.orchestration.worker.model_for", lambda _player: "vendor/paid-model"
    )
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("the free-model allowance for the day is spent (429)", scope=SCOPE_FREE)

    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.RUNNING
    assert await _pauses(db, game.game.id) == []
