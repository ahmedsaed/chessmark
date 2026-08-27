"""A rate limit pauses the game (OPS-12).

**The incident this exists to prevent, in one paragraph.** One free model's only endpoint went hot
for ninety minutes. Each game spent eight provider attempts inside the gateway, was requeued five
times with no delay, spent forty requests in six and a half minutes, and was then marked
*abandoned* at ply 0. The pool, whose matchmaker prioritises whoever it knows least about, then
paired the same model again — because an abandoned game is excluded from ratings, so its deviation
never moved and it was permanently the least-known entrant. Fourteen games in a row, ~560 doomed
requests, all of them charged against the same daily allowance the retries were meant to protect.

So the assertions here are about what *does not* happen: the game is not abandoned, the retry
budget is not spent, and the concurrency slot is not held.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.cooldown import LADDER_SECONDS, ProviderCooldown
from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Player
from chessmark.game import Termination
from chessmark.orchestration.reconciler import resume
from chessmark.orchestration.worker import HUMAN_MAX_PAUSE_SECONDS, PAUSE_WINDOW, PAUSED
from tests.support import Fixture

pytestmark = pytest.mark.integration

#: The model in the incident. Named in the refusal body below; the seat this suite plays is the
#: scripted one, which is what the cooldown is actually keyed by — a refusal is about the endpoint
#: that answered, whatever the message says.
MODEL = "google/gemma-4-26b-a4b-it:free"
SEATED_MODEL = "scripted/white"

#: The body from the incident: a shared-pool refusal naming its provider, carrying no retry hint.
SHARED_POOL_429 = (
    'litellm.RateLimitError: RateLimitError: OpenrouterException - {"error":{"message":'
    '"Provider returned error","code":429,"metadata":{"raw":"' + MODEL + " is temporarily "
    'rate-limited upstream","provider_name":"Google AI Studio","is_byok":false,'
    '"limit_source":"upstream_provider_shared_pool"}}}'
)


class SharedPoolError(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__(SHARED_POOL_429)


async def rate_limited(**_kwargs: object) -> object:
    raise SharedPoolError


class ContextTooSmallError(Exception):
    """A 400 from an endpoint whose window cannot hold what the harness asked for."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__(
            "litellm.BadRequestError: OpenrouterException - This endpoint's maximum context "
            "length is 65536 tokens. However, you requested about 65810 tokens (1430 of text "
            "input, 380 of tool input, 64000 in the output)."
        )


async def context_too_small(**_kwargs: object) -> object:
    raise ContextTooSmallError


async def _events(db: AsyncSession, game_id: Any, type_: EventType) -> list[GameEvent]:
    rows = await db.scalars(
        sa.select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.type == type_)
    )
    return list(rows)


class TestOnePause:
    async def test_the_game_is_paused_not_abandoned(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """The heart of it. `aborted` is a claim that the game will not continue, and it was not
        true — the provider was working and had told us to come back."""
        handled = await make_worker(rate_limited, cooldown=ProviderCooldown(redis)).handle(
            game.first_job
        )

        assert handled.outcome == PAUSED

        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.status is GameStatus.PAUSED
        assert reloaded.termination is None, "a pause is not a termination"
        assert reloaded.resume_after is not None

    async def test_the_reason_reaches_the_page(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """A board that stops moving with nothing to say about why reads as broken. The provider
        and the limit source are both in it, because "rate-limited" alone does not tell you whether
        to wait or to stop."""
        await make_worker(rate_limited, cooldown=ProviderCooldown(redis)).handle(game.first_job)

        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.pause_reason is not None
        assert "Google AI Studio" in reloaded.pause_reason
        assert "upstream_provider_shared_pool" in reloaded.pause_reason

    async def test_exactly_one_event_is_appended(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """Invariant 7. Live, reconnect and replay all read that one table, so a state change the
        log does not carry is a state change the page can never show."""
        await make_worker(rate_limited, cooldown=ProviderCooldown(redis)).handle(game.first_job)

        db.expunge_all()
        events = await _events(db, game.game.id, EventType.GAME_PAUSED)

        assert len(events) == 1
        payload = events[0].payload
        assert payload["provider"] == "Google AI Studio"
        assert payload["limit_source"] == "upstream_provider_shared_pool"
        assert payload["resume_after"]
        assert payload["reason"]

    async def test_the_retry_budget_is_not_spent(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """Nothing is requeued. The old path put the job straight back with no delay, which is how
        one game reached forty provider requests: five job attempts times eight gateway attempts."""
        before = await game.queue.depth()

        await make_worker(rate_limited, cooldown=ProviderCooldown(redis)).handle(game.first_job)

        # Depth, not "the stream is empty": the job that delivered this turn is still in the
        # stream, unacked, because `handle` does not ack — `process` does. What matters is that no
        # *new* job was added, which is exactly what the old path did five times over.
        assert await game.queue.depth() == before

    async def test_the_transcript_is_untouched(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """So resuming is indistinguishable from a first try. The turn is rolled back whole, which
        also means its `turn_started` never reaches the log — the reason a pause notice belongs to
        no turn."""
        from chessmark.agents import transcript

        before = await transcript.build_messages(db, game.white.id)

        await make_worker(rate_limited, cooldown=ProviderCooldown(redis)).handle(game.first_job)

        db.expunge_all()
        assert await transcript.build_messages(db, game.white.id) == before

    async def test_a_paused_game_does_no_work(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """A redelivered job for a paused game must not call the provider again — that would spend
        the requests the pause exists to save."""
        worker = make_worker(rate_limited, cooldown=ProviderCooldown(redis))
        await worker.handle(game.first_job)

        again = await worker.handle(game.first_job)

        assert again.outcome == "not_running"


class TestTheCooldown:
    async def test_the_endpoint_is_cooled_down_for_the_next_game(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """The memory between games, and the part that broke the loop. Without it the next pairing
        rediscovers this at full price, which is what happened fourteen times."""
        cooldown = ProviderCooldown(redis)

        await make_worker(rate_limited, cooldown=cooldown).handle(game.first_job)

        assert await cooldown.resting([SEATED_MODEL]) == {SEATED_MODEL}
        assert await cooldown.remaining(SEATED_MODEL, provider="Google AI Studio") > 0

    async def test_it_pauses_without_a_cooldown_too(
        self, db: AsyncSession, game: Fixture, make_worker: Any
    ) -> None:
        """Optional, like the budget: a scripted test spends nothing and rate-limits nothing.
        Without it a game still pauses — it just pauses on the first rung every time, and nothing
        between the games learns."""
        handled = await make_worker(rate_limited, cooldown=None).handle(game.first_job)

        assert handled.outcome == PAUSED
        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.status is GameStatus.PAUSED
        assert reloaded.resume_after is not None, "still a time to come back to"


class TestPatienceRunsOut:
    async def test_a_game_still_stuck_after_the_window_is_abandoned(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """A permanently dead endpoint must not leave a game open forever. Abandoning is the honest
        end: it says the harness gave up, and it keeps the game out of the ratings rather than
        inventing a loss for a model that never got to play.

        **Patience is a span, not a count.** It was six pauses, which came to a little over three
        hours on the cooldown ladder — and four real games were abandoned in one afternoon because
        two free pools stayed hot for longer than that. A count also tied the patience to the
        ladder's tuning; this is measured from the first pause, so it means what it says.
        """
        worker = make_worker(rate_limited, cooldown=ProviderCooldown(redis))

        # First pause: well inside the window, so the game waits.
        assert (await worker.handle(game.first_job)).outcome == PAUSED
        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None and reloaded.status is GameStatus.PAUSED

        # Backdate that pause to just past the window and let it run again.
        await db.execute(
            sa.update(GameEvent)
            .where(
                GameEvent.game_id == game.game.id,
                GameEvent.type == EventType.GAME_PAUSED,
            )
            .values(created_at=dt.datetime.now(dt.UTC) - PAUSE_WINDOW - dt.timedelta(minutes=1))
        )
        await resume(db, reloaded)
        await db.commit()

        handled = await worker.handle(game.first_job)

        assert handled.outcome == "aborted"
        db.expunge_all()
        final = await db.get(Game, game.game.id)
        assert final is not None
        assert final.status is GameStatus.ABORTED
        assert final.termination is Termination.ABANDONED
        assert final.winner_colour is None, "no model may be blamed for a provider's pool"
        assert final.termination_detail is not None
        assert "pauses" in final.termination_detail

    async def test_a_day_of_patience(self) -> None:
        """The number itself, asserted because it is a policy rather than an implementation detail:
        allowances reset daily and a pool hot at 14:00 is usually serving by morning, so a game that
        cannot get a turn in a day is waiting on something that is not coming back."""
        assert dt.timedelta(hours=24) == PAUSE_WINDOW

    async def test_many_pauses_inside_the_window_keep_waiting(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """The behaviour the change buys. Six pauses used to be fatal; now only the clock is."""
        worker = make_worker(rate_limited, cooldown=ProviderCooldown(redis))

        for _ in range(8):
            assert (await worker.handle(game.first_job)).outcome == PAUSED
            db.expunge_all()
            reloaded = await db.get(Game, game.game.id)
            assert reloaded is not None
            await resume(db, reloaded)
            await db.commit()


class TestAPersonIsWaiting:
    async def test_a_human_game_pauses_only_briefly(
        self, db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
    ) -> None:
        """A person will not wait out a shared pool. The mechanism is the same and the patience is
        not: minutes rather than hours, so their game does not sit open all afternoon while they
        watch a board that will not move.

        Black is made human, so ply 0 is still the model's to play and is the turn that gets
        refused — a pause on a person's own move would be a different bug.
        """
        await db.execute(
            sa.update(Player).where(Player.id == game.black.id).values(kind=PlayerKind.HUMAN)
        )
        await db.commit()

        # Long enough on the model ladder to be obviously different from the human ceiling.
        cooldown = ProviderCooldown(redis)
        for _ in range(len(LADDER_SECONDS)):
            await cooldown.note(SEATED_MODEL, provider="Google AI Studio")

        await make_worker(rate_limited, cooldown=cooldown).handle(game.first_job)

        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.status is GameStatus.PAUSED
        assert reloaded.resume_after is not None
        wait = (reloaded.resume_after - dt.datetime.now(dt.UTC)).total_seconds()
        assert wait <= HUMAN_MAX_PAUSE_SECONDS + 5
        assert wait < LADDER_SECONDS[-1], "a person is not made to wait out the model ladder"


async def _next_seq(db: AsyncSession, game_id: Any) -> int:
    highest = await db.scalar(
        sa.select(sa.func.coalesce(sa.func.max(GameEvent.seq), 0)).where(
            GameEvent.game_id == game_id
        )
    )
    return int(highest or 0) + 1


class TestARejectedRequest:
    """A refusal of the request itself is abandoned at once, not five times.

    The game this comes from played ten plies of a real Scotch Game and then died on:

        This endpoint's maximum context length is 65536 tokens. However, you requested about
        65810 tokens (1430 of text input, 380 of tool input, 64000 in the output).

    The gateway classified it correctly and tried once. The worker then requeued the job four more
    times, because a `TurnResult` carried the error's *text* and nothing that could be reasoned
    about — five identical rejections before it gave up.
    """

    async def test_it_is_abandoned_without_a_retry(
        self, db: AsyncSession, game: Fixture, make_worker: Any
    ) -> None:
        handled = await make_worker(context_too_small).handle(game.first_job)

        assert handled.outcome == "aborted"
        assert await game.queue.depth() == await game.queue.depth(), "nothing was requeued"

        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.status is GameStatus.ABORTED
        assert reloaded.termination is Termination.ABANDONED
        assert reloaded.termination_detail is not None
        assert "rejected the request" in reloaded.termination_detail

    async def test_it_is_not_a_forfeit(
        self, db: AsyncSession, game: Fixture, make_worker: Any
    ) -> None:
        """The model asked for a completion the *harness* sized. Blaming it for our `max_tokens`
        would publish a loss for a model that never had a chance to move."""
        await make_worker(context_too_small).handle(game.first_job)

        db.expunge_all()
        reloaded = await db.get(Game, game.game.id)
        assert reloaded is not None
        assert reloaded.winner_colour is None
        seats = await db.scalars(sa.select(Player).where(Player.game_id == game.game.id))
        assert not any(seat.forfeited for seat in seats)

    async def test_an_outage_still_gets_its_retries(
        self, db: AsyncSession, game: Fixture, make_worker: Any
    ) -> None:
        """The distinction that makes this safe. A 503 is a provider having a bad minute and the
        next attempt may well work; a 400 is the same bytes being refused the same way."""

        class OutageError(Exception):
            status_code = 503

        async def unavailable(**_kwargs: object) -> object:
            raise OutageError

        handled = await make_worker(unavailable).handle(game.first_job)

        assert handled.outcome == "turn_failed", "requeued, not abandoned"
