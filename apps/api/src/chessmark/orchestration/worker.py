"""The turn worker.

Consumes `advance_turn` jobs and plays one turn each. Stateless: every job rebuilds the position
from Postgres, so any worker can pick up any game and none of them hold anything between jobs.

Three properties make this survivable, all from ADR-0007:

* **Idempotent.** A job carries `expected_ply`. If the game has already moved past it, the job is
  a no-op. At-least-once delivery is therefore fine, and a redelivery is harmless.
* **One transaction per turn.** Everything a turn produces — the turn row, the LLM calls, the tool
  calls, the transcript, the ply, the events — commits together or not at all (NFR-08). A crash
  mid-turn rolls back to a clean state rather than leaving a half-written turn.
* **Ack after commit.** The job stays in the consumer group's pending list until the turn is
  durable. Kill the worker mid-turn and another one reclaims the job and reruns it. The cost is at
  most one wasted LLM call; the alternative — acking first — silently loses turns.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.llm import LlmGateway
from chessmark.agents.routing import ProviderRouting
from chessmark.agents.turn import TurnLimits, TurnResult, TurnRunner
from chessmark.core.budget import GlobalBudget
from chessmark.core.cooldown import ProviderCooldown, resume_at
from chessmark.db.enums import EventType, GameStatus, PlayerKind, TurnStatus
from chessmark.db.models import Game, GameEvent, Player
from chessmark.db.quotas import record_spend
from chessmark.db.repositories import (
    append_event,
    finish_game,
    get_game,
    load_events,
    rebuild_referee,
)
from chessmark.game import Colour, GameResult, Outcome, Referee, Termination
from chessmark.orchestration.match import model_for
from chessmark.orchestration.queue import AdvanceTurn, Delivery, TurnQueue

log = logging.getLogger(__name__)

#: Redis pub/sub channel per game. Postgres is the durable record; this is only the notification
#: path, so a dropped message costs latency and never data (ADR-0008).
EVENT_CHANNEL = "chessmark:game:{game_id}"


class TurnOutcome(str):
    """Why the worker stopped handling a job. Used for logging and tests."""


ADVANCED = TurnOutcome("advanced")
STALE = TurnOutcome("stale")
GAME_OVER = TurnOutcome("game_over")
NOT_RUNNING = TurnOutcome("not_running")
TURN_FAILED = TurnOutcome("turn_failed")
BUDGET = TurnOutcome("budget_exceeded")
#: The global kill switch was tripped. The turn is not run and the game is left RUNNING, so it
#: resumes when the budget resets rather than being forfeited for an outage of our own making.
GLOBAL_BUDGET = TurnOutcome("global_budget_halted")
#: The side to move is a person. The worker does nothing and enqueues nothing — the game waits in
#: RUNNING until the human's move endpoint commits a ply and enqueues the model's reply. Anything
#: else would run an LLM turn on a human's behalf and play their move for them.
AWAITING_HUMAN = TurnOutcome("awaiting_human")
ABORTED = TurnOutcome("aborted")
#: The provider asked us to come back later. The game is paused with a time to resume at, holds no
#: concurrency slot while it waits, and is picked up again by the reconciler.
PAUSED = TurnOutcome("paused")

#: How many times a turn may be retried after a provider failure before the game is abandoned.
#: Generous, because the failures this covers — outages, mangled responses — are usually temporary.
#: Rate limits no longer come through here at all; they pause the game instead.
MAX_JOB_ATTEMPTS = 5

#: How many times one game may be paused for a rate limit before it is abandoned. With the cooldown
#: ladder in `core/cooldown.py` this is a little over three hours of patience, which is long enough
#: to outlast a hot shared pool and short enough that a permanently dead endpoint does not leave a
#: game open forever.
MAX_PAUSES = 6

#: A person will not wait out a provider. A game with a human seat pauses briefly, twice, and is
#: then abandoned rather than left open — the honest outcome, and better than a board that quietly
#: never moves again. Their opponent's model is not forfeited: nobody played badly.
HUMAN_MAX_PAUSE_SECONDS = 120
HUMAN_MAX_PAUSES = 2


def _pinned_provider(player: Player) -> str | None:
    """The endpoint this seat is pinned to, if it is (ADR-0015).

    Derived from `provider_routing.only` rather than stored, which is the same thing the API does.
    Used only as a fallback: the provider named in the refusal itself is better evidence, because
    it is the endpoint that actually answered.
    """
    only = (player.provider_routing or {}).get("only") or []
    return str(only[0]) if only else None


class ProviderFailureError(Exception):
    """Raised to roll a turn back after a provider error.

    Rolling back matters more than it first appears. By the time a call fails, the turn has
    already appended its turn prompt (and possibly assistant and tool messages) to the transcript.
    Committing that and then retrying would append a *second* turn prompt, so the model would see
    "It is your move. Ply 7" twice with a dead exchange between them. Discarding the whole turn
    leaves the transcript exactly as it was, and the retry is indistinguishable from a first try.
    """

    def __init__(self, result: TurnResult) -> None:
        super().__init__(result.error or "provider failure")
        self.result = result


@dataclass(slots=True)
class HandledJob:
    outcome: TurnOutcome
    game_id: uuid.UUID
    ply: int
    result: TurnResult | None = None
    game_outcome: Outcome | None = None


class TurnWorker:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        queue: TurnQueue,
        gateway: LlmGateway,
        redis: Redis[Any] | None = None,
        limits: TurnLimits | None = None,
        consumer: str | None = None,
        budget: GlobalBudget | None = None,
        cooldown: ProviderCooldown | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.queue = queue
        self.gateway = gateway
        self.redis = redis
        self.limits = limits
        #: Layer 1 of ADR-0011. Optional so scripted tests, which spend nothing, need not wire it.
        self.budget = budget
        #: What is remembered between games about an endpoint that refused. Optional for the same
        #: reason: a scripted provider never rate-limits anything. Without it a game still pauses
        #: — it just pauses on the first rung every time, and the matchmaker learns nothing.
        self.cooldown = cooldown
        self.consumer = consumer or f"worker-{uuid.uuid4().hex[:8]}"
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ loop

    async def run_forever(self, *, reclaim_every: int = 20) -> None:
        await self.queue.ensure_group()
        cycles = 0

        while not self._stopping.is_set():
            cycles += 1
            deliveries = await self.queue.consume(self.consumer, block_ms=2000)

            if not deliveries and cycles % reclaim_every == 0:
                deliveries = await self.queue.reclaim_stalled(self.consumer)

            for delivery in deliveries:
                await self.process(delivery)

    def stop(self) -> None:
        self._stopping.set()

    async def process(self, delivery: Delivery) -> HandledJob:
        """Handle one delivery and acknowledge it.

        The ack happens in `finally`: a job that fails for a reason rerunning cannot fix would
        otherwise be redelivered forever, blocking the queue behind it.
        """
        try:
            return await self.handle(delivery.job)
        finally:
            await self.queue.ack(delivery.message_id)

    # ------------------------------------------------------------------ one turn

    async def handle(self, job: AdvanceTurn) -> HandledJob:
        try:
            return await self._advance(job)
        except ProviderFailureError as failure:
            # A rate limit is not a failure to retry harder at. The provider is working and has
            # told us to come back; the position is untouched. Burning the job's retry budget on
            # it spent forty requests a game and then abandoned fourteen games in a row.
            if failure.result.rate_limit is not None:
                return await self._pause(job, failure.result)
            return await self._retry_or_abandon(job, failure.result)

    async def _advance(self, job: AdvanceTurn) -> HandledJob:
        async with self.sessionmaker() as session, session.begin():
            game = await get_game(session, job.game_id)

            if game.status is not GameStatus.RUNNING:
                return HandledJob(NOT_RUNNING, game.id, game.ply_count)

            referee = await rebuild_referee(session, game)

            # The idempotency check. A redelivered job whose ply has already been played is
            # simply dropped — this is what makes at-least-once delivery safe (ADR-0007).
            if referee.ply != job.expected_ply:
                log.info(
                    "dropping stale job for %s: expected ply %s, game is at %s",
                    game.id,
                    job.expected_ply,
                    referee.ply,
                )
                return HandledJob(STALE, game.id, referee.ply)

            if referee.is_over:
                await self._conclude(session, game, referee.outcome)
                return HandledJob(GAME_OVER, game.id, referee.ply, game_outcome=referee.outcome)

            over_budget = await self._enforce_budget(session, game, referee)
            if over_budget is not None:
                return HandledJob(BUDGET, game.id, referee.ply, game_outcome=over_budget)

            # Layer 1, checked here rather than in the API because this is the last point before
            # money is actually spent — a game admitted an hour ago must not keep spending into a
            # budget that has since run out (AUTH-05).
            if self.budget is not None and await self.budget.tripped():
                log.warning(
                    "global daily budget reached; halting turn for %s at ply %s",
                    game.id,
                    referee.ply,
                )
                # Deliberately *not* re-enqueued and *not* forfeited. The game stays RUNNING with
                # its job dropped; the reconciler picks it up as stalled once spending is possible
                # again. Forfeiting a model for our budget would corrupt the benchmark.
                return HandledJob(GLOBAL_BUDGET, game.id, referee.ply)

            colour = referee.side_to_move
            player = await self._player(session, game.id, colour)
            opponent = await self._player(session, game.id, colour.opponent)

            # A person is to move. Stop here, and crucially do **not** re-enqueue: a job that
            # requeued itself would spin at the poll interval for as long as the human took to
            # think. The move endpoint enqueues the model's turn when the ply lands (HUMAN-02).
            if PlayerKind(player.kind) is not PlayerKind.MODEL:
                return HandledJob(AWAITING_HUMAN, game.id, referee.ply)

            # Route by *this player's* resolved policy. Per player rather than per game because
            # `only` names providers and providers are model-specific: one vendor's endpoint list
            # is a 404 for the other seat's model.
            self.gateway.routing = ProviderRouting.from_record(
                player.provider_routing or game.provider_routing
            )

            runner = TurnRunner(
                session,
                gateway=self.gateway,
                referee=referee,
                game=game,
                player=player,
                opponent=opponent,
                model=model_for(player),
                limits=self.limits,
            )
            before_seq = game.event_seq
            result = await runner.run()

            # Roll this turn's spend into the day's counters. Both are best-effort relative to the
            # turn: the money is already spent, so a failure to record must not undo the game.
            await self._record_spend(session, game, result)

            # A provider failure is ours, not the model's (AGENT-09). Raising discards the whole
            # turn so the retry starts from an untouched transcript.
            if result.status is TurnStatus.FAILED and result.outcome is None:
                raise ProviderFailureError(result)

            # It served a whole turn, so whatever it refused earlier is over. Without this the
            # cooldown ladder only ever climbs, and an endpoint that was briefly hot last night
            # would rest for an hour over its next single refusal.
            if self.cooldown is not None:
                await self.cooldown.clear(model_for(player), provider=_pinned_provider(player))

            if referee.is_over:
                await self._conclude(session, game, referee.outcome)

            events = await load_events(session, game.id, after_seq=before_seq)

        # Committed. Only now is it safe to tell anyone about it.
        await self._publish(job.game_id, events)

        # Enqueue the next turn only if a *model* is to play it. Handing the queue a job for a
        # human's move produces a job the worker can only answer with `awaiting_human`, and it
        # lingers in the stream as a stale entry that the human's own follow-up job then queues
        # behind. The move endpoint enqueues when the person's ply actually lands.
        next_player = player if referee.side_to_move is colour else opponent
        if not referee.is_over and PlayerKind(next_player.kind) is PlayerKind.MODEL:
            await self.queue.enqueue(AdvanceTurn(game_id=job.game_id, expected_ply=referee.ply))

        return HandledJob(
            ADVANCED if not referee.is_over else GAME_OVER,
            job.game_id,
            referee.ply,
            result=result,
            game_outcome=referee.outcome,
        )

    async def _pause(self, job: AdvanceTurn, result: TurnResult) -> HandledJob:
        """Stop the game until the provider will serve it again.

        Three things happen, and the order is not arbitrary. The **cooldown** is recorded first,
        because it is what stops the next game from rediscovering this at full price — a pool
        paired one dark model fourteen times because nothing between the games remembered. Then the
        game is **paused**, which frees the concurrency slot it was holding: `in_flight` counts
        games that are `PENDING` or `RUNNING`, so a pool with a concurrency of one can get on with
        an entrant that can actually play. Finally the pause is **appended to the event log**, so
        the page can say why the board stopped instead of simply stopping (ADR-0008, invariant 7).

        The turn itself was already rolled back by `ProviderFailureError`, so the transcript is
        exactly as it was before the attempt and resuming is indistinguishable from a first try.
        """
        limit = result.rate_limit
        assert limit is not None  # only reached from the rate-limit branch of `handle`

        async with self.sessionmaker() as session, session.begin():
            game = await get_game(session, job.game_id)
            colour = Colour(("white", "black")[job.expected_ply % 2])
            player = await self._player(session, game.id, colour)
            model = model_for(player)
            provider = limit.provider or _pinned_provider(player)

            # A person is not going to wait out a shared pool, so their game gets minutes rather
            # than hours. Everything else about the mechanism is the same.
            human = await self._has_human_seat(session, game.id)
            max_pauses = HUMAN_MAX_PAUSES if human else MAX_PAUSES
            pauses = await self._pause_count(session, game.id)

            seconds = 0
            if self.cooldown is not None:
                seconds = await self.cooldown.note(
                    model,
                    provider=provider,
                    retry_after_seconds=limit.retry_after_seconds,
                    # A shared pool is the provider's, not this model's. Resting only the model let
                    # the matchmaker skip it and pair its neighbour on the same hot pool, which
                    # paused for the same reason a minute later — three models, four paused games.
                    shared_pool=limit.is_upstream_pool,
                )
            seconds = max(seconds, 60)
            if human:
                seconds = min(seconds, HUMAN_MAX_PAUSE_SECONDS)

            source = limit.limit_source or "rate limit"
            reason = f"{model} rate-limited by {provider or 'its provider'} ({source})"

            # Patience spent. Abandoned rather than left paused, because a game nobody will ever
            # resume is worse open than closed — and abandoning is honest: it says the harness gave
            # up, and it keeps the result out of the ratings rather than inventing a loss.
            if pauses + 1 > max_pauses:
                log.error(
                    "abandoning %s at ply %s: %s, still rate-limited after %s pauses",
                    game.id,
                    job.expected_ply,
                    reason,
                    pauses,
                )
                await self._abandon(session, game, f"Abandoned after {pauses} pauses: {reason}")
                return HandledJob(ABORTED, game.id, job.expected_ply, result=result)

            game.status = GameStatus.PAUSED
            game.resume_after = resume_at(seconds)
            game.pause_reason = reason
            log.warning(
                "pausing %s at ply %s for %ss (pause %s/%s): %s",
                game.id,
                job.expected_ply,
                seconds,
                pauses + 1,
                max_pauses,
                reason,
            )
            await append_event(
                session,
                game_id=game.id,
                type=EventType.GAME_PAUSED,
                payload={
                    "reason": reason,
                    "player_id": str(player.id),
                    "colour": colour.value,
                    "model": model,
                    "provider": provider,
                    "limit_source": limit.limit_source,
                    "retry_after_seconds": limit.retry_after_seconds,
                    "resume_after": game.resume_after.isoformat(),
                    "seconds": seconds,
                    "pause": pauses + 1,
                    "max_pauses": max_pauses,
                },
            )
            before_seq = game.event_seq - 1
            events = await load_events(session, game.id, after_seq=before_seq)

        # Published after the commit, like every other event: a subscriber must never be told about
        # a state the database has not accepted.
        await self._publish(job.game_id, events)
        return HandledJob(PAUSED, job.game_id, job.expected_ply, result=result)

    async def _pause_count(self, session: AsyncSession, game_id: uuid.UUID) -> int:
        """How many times this game has already been paused.

        Counted from the event log rather than a column on the game. The log is append-only and is
        already the authority on what happened (ADR-0008), so a counter beside it would be a second
        copy of one fact with its own way of being wrong.
        """
        count = await session.scalar(
            sa.select(sa.func.count(GameEvent.id)).where(
                GameEvent.game_id == game_id, GameEvent.type == EventType.GAME_PAUSED
            )
        )
        return int(count or 0)

    async def _has_human_seat(self, session: AsyncSession, game_id: uuid.UUID) -> bool:
        seats = await session.scalars(sa.select(Player.kind).where(Player.game_id == game_id))
        return any(PlayerKind(kind) is not PlayerKind.MODEL for kind in seats)

    async def _abandon(self, session: AsyncSession, game: Game, detail: str) -> None:
        """Close a game the harness could not finish. Never a chess result, never a forfeit."""
        game.status = GameStatus.ABORTED
        game.termination = Termination.ABANDONED
        game.termination_detail = detail
        game.ended_at = sa.func.now()
        await append_event(
            session,
            game_id=game.id,
            type=EventType.GAME_ENDED,
            payload={
                "result": str(GameResult.ONGOING),
                "termination": str(Termination.ABANDONED),
                "detail": detail,
                "winner": None,
            },
        )

    async def _retry_or_abandon(self, job: AdvanceTurn, result: TurnResult) -> HandledJob:
        """Re-enqueue the same ply, or give up on the game.

        Giving up **abandons** the game rather than forfeiting a player: nobody played badly, our
        provider was unavailable. An abandoned game is excluded from ratings rather than counted
        as a loss.
        """
        # A rejected *request* is not a flaky provider, and the retry budget cannot fix it: the
        # next attempt sends the same bytes and is refused the same way. One game spent five
        # attempts being told its 64,000-token completion did not fit a 65,536-token window.
        if result.request_rejected:
            log.error(
                "abandoning %s at ply %s: the provider rejected the request itself: %s",
                job.game_id,
                job.expected_ply,
                result.error,
            )
            async with self.sessionmaker() as session, session.begin():
                game = await get_game(session, job.game_id)
                await self._abandon(
                    session, game, f"Abandoned — the provider rejected the request: {result.error}"
                )
            return HandledJob(ABORTED, job.game_id, job.expected_ply, result=result)

        if job.attempt < MAX_JOB_ATTEMPTS:
            log.warning(
                "provider failure on %s ply %s (attempt %s/%s), requeueing: %s",
                job.game_id,
                job.expected_ply,
                job.attempt,
                MAX_JOB_ATTEMPTS,
                result.error,
            )
            await self.queue.enqueue(job.next_attempt())
            return HandledJob(TURN_FAILED, job.game_id, job.expected_ply, result=result)

        log.error(
            "abandoning %s after %s failed attempts at ply %s: %s",
            job.game_id,
            job.attempt,
            job.expected_ply,
            result.error,
        )
        async with self.sessionmaker() as session, session.begin():
            game = await get_game(session, job.game_id)
            await self._abandon(
                session,
                game,
                f"Abandoned after {job.attempt} failed provider attempts: {result.error}",
            )

        return HandledJob(ABORTED, job.game_id, job.expected_ply, result=result)

    # ------------------------------------------------------------------ helpers

    async def _player(self, session: AsyncSession, game_id: uuid.UUID, colour: Colour) -> Player:
        player = await session.scalar(
            sa.select(Player).where(Player.game_id == game_id, Player.colour == colour)
        )
        if player is None:
            msg = f"game {game_id} has no {colour.value} player"
            raise LookupError(msg)
        return player

    async def _record_spend(self, session: AsyncSession, game: Game, result: TurnResult) -> None:
        """Add a turn's cost to the global counter and to the owner's daily ledger.

        Costs come from `result.cost_usd`, which is computed from the token counts the provider
        actually returned — never estimated (invariant 4).
        """
        if result.cost_usd <= 0:
            return

        if self.budget is not None:
            await self.budget.record(result.cost_usd)

        if game.created_by_user_id is not None:
            await record_spend(session, game.created_by_user_id, result.cost_usd)

    async def _enforce_budget(
        self, session: AsyncSession, game: Game, referee: Referee
    ) -> Outcome | None:
        """Layer 3 of ADR-0011: the per-game USD cap.

        Checked before a turn rather than after, because the point is to stop spending — noticing
        afterwards means the money is already gone. A game stopped this way is a draw: neither
        model did anything wrong, and awarding the win to whoever happened to be ahead would put
        our budgeting decision into the benchmark results.
        """
        if game.max_usd is None or Decimal(game.total_cost_usd) < Decimal(game.max_usd):
            return None

        outcome = referee.adjudicate(
            GameResult.DRAW,
            f"Stopped after ${game.total_cost_usd} of a ${game.max_usd} budget.",
            termination=Termination.BUDGET_EXCEEDED,
        )
        await self._conclude(session, game, outcome)
        return outcome

    async def _conclude(self, session: AsyncSession, game: Game, outcome: Outcome | None) -> None:
        if outcome is None or game.status is GameStatus.FINISHED:
            return

        await finish_game(session, game_id=game.id, outcome=outcome)
        await append_event(
            session,
            game_id=game.id,
            type=EventType.GAME_ENDED,
            payload={
                "result": str(outcome.result),
                "termination": str(outcome.termination),
                "detail": outcome.detail,
                "winner": outcome.winner.value if outcome.winner else None,
                "ply_count": game.ply_count,
                "total_cost_usd": str(game.total_cost_usd),
            },
        )

    async def _publish(self, game_id: uuid.UUID, events: list[GameEvent]) -> None:
        """Fan out committed events for live spectators (ADR-0008).

        Best-effort by design: Postgres already holds them, so a client that misses a message
        recovers by replaying from `game_events`. A publish failure must never fail a turn that
        has already committed.
        """
        if self.redis is None or not events:
            return

        channel = EVENT_CHANNEL.format(game_id=game_id)
        for event in events:
            payload = {
                "seq": event.seq,
                "type": str(event.type),
                "payload": event.payload,
            }
            with contextlib.suppress(Exception):
                await self.redis.publish(channel, json.dumps(payload))
