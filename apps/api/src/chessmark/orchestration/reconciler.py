"""Rescue games that stopped moving.

The queue's own `XAUTOCLAIM` covers the common crash: a worker died holding a job, another takes
it over. This covers the case it cannot — a job that no longer exists at all, because Redis was
restarted or flushed, or because a bug dropped it.

Postgres is the authority on what should be running (ADR-0008), so the reconciler asks it rather
than the queue: any game marked `running` whose last event is older than a threshold is stalled,
and gets a fresh `advance_turn` for wherever it actually is. Enqueuing one for a game that turns
out to be fine is harmless — `expected_ply` makes the duplicate a no-op.

It also **resumes paused games**, which is the same question asked of a different status: a game
paused for a provider rate limit is waiting on a clock rather than on a worker, and when that clock
passes somebody has to put it back on the queue. This is that somebody, and it lives here because
the alternative is a second loop asking Postgres the same thing.

Resuming respects the event's concurrency bound, which is less obvious than it sounds: a paused game
holds no slot (ADR-0017), so coming back it has to ask for one. Whatever does not fit stays paused
and is picked up on a later tick.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.core.credits import fetch_balance
from chessmark.core.halt import Halt
from chessmark.db import tournaments as repo
from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Player, Tournament, TournamentGame
from chessmark.db.repositories import append_event, finish_game, rebuild_referee
from chessmark.game import Colour, GameResult, Outcome, Termination
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue

log = logging.getLogger(__name__)

#: How long a game may go without an event before it is treated as stalled.
#:
#: **Tuning, not the guarantee.** It was 20 minutes, and since the per-call timeout became ten
#: minutes (ADR-0017) a healthy slow turn can legitimately exceed that — `max_tool_iterations` is
#: 20 — so this sweep was *manufacturing* the duplicate jobs that had two workers playing ply 19
#: fifty milliseconds apart. What makes a duplicate harmless is the row lock in `worker._advance`
#: (ADR-0022); this number only decides how often we make one and throw it away.
#:
#: Deliberately below the worst legitimate turn. A duplicate is now dropped in microseconds, and
#: the alternative — a threshold above every possible turn — leaves a genuinely stalled game
#: invisible for hours.
DEFAULT_STALE_AFTER = dt.timedelta(minutes=45)

#: How long a game may sit waiting for a person before it is written off (GAME-08).
#: Deliberately far longer than the stall threshold: a stalled *model* game is our fault and
#: should be rescued in minutes, while a person is entitled to leave the tab open over lunch.
DEFAULT_HUMAN_IDLE_AFTER = dt.timedelta(hours=2)


#: How often the credit probe runs while a credit halt stands.
#:
#: One request every five minutes, not one per game per tick. The halt exists precisely because the
#: account cannot serve requests, so the probe must not become the thing it was built to prevent.
#: A top-up is noticed within five minutes, which is faster than anybody watching for it.
CREDIT_PROBE_EVERY = dt.timedelta(minutes=5)

#: The Redis key remembering when the probe last ran, so several reconcilers share one cadence.
CREDIT_PROBE_KEY = "chessmark:halt:probed"


@dataclass(slots=True)
class ReconcileReport:
    requeued: list[str] = field(default_factory=list)
    abandoned: list[str] = field(default_factory=list)
    #: Waiting on a person, inside the idle window. Neither stalled nor abandoned.
    waiting: list[str] = field(default_factory=list)
    #: Paused for a provider rate limit, and put back on the queue because the wait is over.
    resumed: list[str] = field(default_factory=list)
    #: The global halt was lifted this tick, because the account has credit again.
    unhalted: bool = False
    checked: int = 0

    def __str__(self) -> str:
        return (
            f"checked {self.checked} running games, requeued {len(self.requeued)}, "
            f"abandoned {len(self.abandoned)}, waiting on a person {len(self.waiting)}, "
            f"resumed {len(self.resumed)}" + (", lifted the halt" if self.unhalted else "")
        )


class SingleFlight:
    """A lock that lets one worker at a time run a sweep the others would only duplicate.

    Every worker runs a reconciler, and with one worker that was free. With several, they wake
    together and ask Postgres the same question: mostly harmless, because enqueuing is idempotent
    on `expected_ply` — but `with_room_to_run` is not, since two reconcilers each see the same free
    slot and each fill it, admitting more running games than the event allows.

    A Redis key with a TTL rather than a real lock. If a holder dies the key expires and the next
    sweep proceeds, which is the correct failure: a missed sweep costs a minute, and a lock nobody
    can release costs everything after it.
    """

    def __init__(self, redis: Any, *, key: str = "chessmark:reconcile:lock", ttl: int = 55) -> None:
        self._redis = redis
        self._key = key
        self._ttl = ttl

    async def __aenter__(self) -> bool:
        if self._redis is None:
            return True
        self._held = bool(await self._redis.set(self._key, "1", nx=True, ex=self._ttl))
        return self._held

    async def __aexit__(self, *_exc: object) -> None:
        if self._redis is not None and getattr(self, "_held", False):
            await self._redis.delete(self._key)


async def find_stalled(
    session: AsyncSession, *, stale_after: dt.timedelta = DEFAULT_STALE_AFTER
) -> list[Game]:
    """Running games whose most recent event is older than `stale_after`.

    Games that have never emitted an event count as stalled too — one created and marked running
    but never enqueued would otherwise sit forever.
    """
    cutoff = dt.datetime.now(dt.UTC) - stale_after
    latest_event = (
        sa.select(
            GameEvent.game_id.label("game_id"),
            sa.func.max(GameEvent.created_at).label("last_at"),
        )
        .group_by(GameEvent.game_id)
        .subquery()
    )

    query = (
        sa.select(Game)
        .outerjoin(latest_event, latest_event.c.game_id == Game.id)
        .where(
            Game.status == GameStatus.RUNNING,
            sa.or_(
                latest_event.c.last_at.is_(None),
                latest_event.c.last_at < cutoff,
            ),
        )
        .order_by(Game.created_at)
    )

    return list(await session.scalars(query))


async def waiting_on_human(session: AsyncSession, game: Game) -> bool:
    """Is this game simply waiting for a person to move?

    Such a game looks exactly like a stalled one from the outside — status `running`, no events —
    and requeueing it does nothing, because the worker sees a human to move and stops. Left
    undistinguished, a human game would be requeued every cycle for as long as it existed and
    would never be written off.

    Whose turn it is comes from replaying the plies rather than from ply parity, because a game
    may start from an arbitrary FEN where parity says the wrong thing.
    """
    human = await session.scalar(
        sa.select(Player).where(Player.game_id == game.id, Player.kind == PlayerKind.HUMAN)
    )
    if human is None:
        return False

    referee = await rebuild_referee(session, game)
    return not referee.is_over and referee.side_to_move is Colour(human.colour)


async def abandon(session: AsyncSession, game: Game) -> None:
    """Write off a game nobody came back to (GAME-08).

    `ABANDONED` rather than a forfeit, and therefore excluded from ratings: walking away from a
    casual game is not a result, and the model on the other side did nothing to earn a win. Human
    games are unranked anyway, so this is about the record being honest rather than about scores.
    """
    outcome = Outcome(
        result=GameResult.ONGOING,
        termination=Termination.ABANDONED,
        winner=None,
        detail="Abandoned: nobody moved for long enough that the game was written off.",
    )
    await finish_game(session, game_id=game.id, outcome=outcome)
    # `finish_game` marks it FINISHED; an abandoned game never reached a result, so it is aborted.
    game.status = GameStatus.ABORTED
    await append_event(
        session,
        game_id=game.id,
        type=EventType.GAME_ENDED,
        payload={
            "result": str(outcome.result),
            "termination": str(outcome.termination),
            "detail": outcome.detail,
            "winner": None,
            "ply_count": game.ply_count,
            "total_cost_usd": str(game.total_cost_usd),
        },
    )
    await session.flush()


async def find_resumable(session: AsyncSession, *, now: dt.datetime | None = None) -> list[Game]:
    """Paused games whose wait is over.

    A pause with no `resume_after` is included deliberately. It should not happen — the worker
    always sets one — but a game paused by a version that did not, or by a hand-edited row, would
    otherwise wait forever, and the failure mode of a stuck game is silence.
    """
    clock = now or dt.datetime.now(dt.UTC)
    rows = await session.scalars(
        sa.select(Game)
        .where(
            Game.status == GameStatus.PAUSED,
            sa.or_(Game.resume_after.is_(None), Game.resume_after <= clock),
        )
        # Longest wait first. When several are due at once the order decides who gets a slot, and
        # "whoever has waited longest" is the only ordering that cannot starve a game.
        .order_by(Game.resume_after.asc().nulls_first())
    )
    return list(rows)


async def with_room_to_run(session: AsyncSession, games: list[Game]) -> list[Game]:
    """Of these, the ones their event has room to actually play.

    **A pause frees the concurrency slot; resuming has to ask for it back.** It did not. `reconcile`
    resumed every due game in one pass and enqueued them all, and nothing consulted
    `max_concurrent` on the way in — so a pool bounded to one game had three paused games come due
    within a quarter of an hour and would have run them in parallel. The bound was honoured
    everywhere except the one path that creates running games without going through `_start_games`.

    Games left over stay paused with `resume_after` already behind them, so the next tick picks
    them up: the effect is that they queue rather than pile in. A game belonging to no event —
    a human's game, anything started by hand — is bounded by nothing and resumes immediately.
    """
    ready: list[Game] = []
    budget: dict[uuid.UUID, int] = {}

    for game in games:
        pairing = await session.scalar(
            sa.select(TournamentGame).where(TournamentGame.game_id == game.id)
        )
        if pairing is None:
            ready.append(game)
            continue

        if pairing.tournament_id not in budget:
            tournament = await session.get(Tournament, pairing.tournament_id)
            if tournament is None:  # pragma: no cover - a pairing without its event
                ready.append(game)
                continue
            running = len(await repo.in_flight(session, tournament.id))
            budget[pairing.tournament_id] = tournament.max_concurrent - running

        if budget[pairing.tournament_id] > 0:
            budget[pairing.tournament_id] -= 1
            ready.append(game)
        else:
            log.info(
                "%s is due to resume but its event is already at its concurrency bound; waiting",
                game.id,
            )
    return ready


async def resume(session: AsyncSession, game: Game) -> AdvanceTurn:
    """Put a paused game back into play. Appends one event, like every other state change."""
    was = game.pause_reason
    game.status = GameStatus.RUNNING
    game.resume_after = None
    game.pause_reason = None
    await append_event(
        session,
        game_id=game.id,
        type=EventType.GAME_RESUMED,
        payload={"detail": f"the wait is over: {was}" if was else "resumed after a pause"},
    )
    return AdvanceTurn(game_id=game.id, expected_ply=game.ply_count)


async def lift_credit_halt(halt: Halt, *, api_key: str, redis: Any = None) -> bool:
    """Lift a halt set by a 402, once the account has credit again. True when it was lifted.

    **Only a credit halt.** An operator halt is never lifted here: somebody meant it, and a probe
    deciding otherwise would be the system overruling the person who stopped it.

    Every uncertainty leaves the halt standing. No key, a probe that times out, a body in an
    unexpected shape, a balance of zero — all of them mean "we did not learn that there is money",
    which is not the same as learning there is, and only the second lifts a stop.
    """
    state = await halt.state()
    if state is None or not state.self_clearing:
        return False

    if redis is not None and not await _probe_is_due(redis):
        return False

    balance = await fetch_balance(api_key)
    if balance is None or not balance.positive:
        return False

    log.warning("account holds $%s again; lifting the credit halt", balance.remaining)
    return await halt.clear()


async def _probe_is_due(redis: Any) -> bool:
    """Rate-limit the probe across every reconciler, using the key's own TTL as the clock.

    `SET NX EX` rather than a timestamp comparison: the key existing *is* "asked recently", so
    there is nothing to parse, nothing to expire by hand, and two reconcilers waking together
    cannot both decide it is due.
    """
    seconds = int(CREDIT_PROBE_EVERY.total_seconds())
    return bool(await redis.set(CREDIT_PROBE_KEY, "1", nx=True, ex=seconds))


async def reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: TurnQueue,
    *,
    stale_after: dt.timedelta = DEFAULT_STALE_AFTER,
    human_idle_after: dt.timedelta = DEFAULT_HUMAN_IDLE_AFTER,
    halt: Halt | None = None,
    api_key: str = "",
    redis: Any = None,
) -> ReconcileReport:
    report = ReconcileReport()
    jobs: list[AdvanceTurn] = []

    # First, because everything below it is pointless while the harness is stopped: a game
    # re-enqueued under a halt is a job the worker can only answer with `halted`.
    if halt is not None:
        report.unhalted = await lift_credit_halt(halt, api_key=api_key, redis=redis)
        if await halt.active():
            log.info("harness is halted; skipping the sweep")
            return report

    async with sessionmaker() as session, session.begin():
        stalled = await find_stalled(session, stale_after=stale_after)
        report.checked = len(stalled)

        # A game waiting on a person is not stalled. It is either still theirs to play, or it has
        # been idle long enough to write off — never something to requeue.
        idle_cutoff = dt.datetime.now(dt.UTC) - human_idle_after
        for game in stalled:
            if await waiting_on_human(session, game):
                last = await _last_event_at(session, game)
                if last is None or last < idle_cutoff:
                    await abandon(session, game)
                    report.abandoned.append(str(game.id))
                    log.info("abandoned idle human game %s at ply %s", game.id, game.ply_count)
                else:
                    report.waiting.append(str(game.id))
                continue

            jobs.append(AdvanceTurn(game_id=game.id, expected_ply=game.ply_count))

        # Resumed in the same transaction as the stall sweep, so one pass over the database
        # decides everything and two callers cannot resume the same game twice.
        resumable = await with_room_to_run(session, await find_resumable(session))
        for game in resumable:
            log.info("resuming %s at ply %s: %s", game.id, game.ply_count, game.pause_reason)
            jobs.append(await resume(session, game))
            report.resumed.append(str(game.id))

    for job in jobs:
        await queue.enqueue(job)
        if str(job.game_id) not in report.resumed:
            report.requeued.append(str(job.game_id))
            log.info("requeued stalled game %s at ply %s", job.game_id, job.expected_ply)

    return report


async def _last_event_at(session: AsyncSession, game: Game) -> dt.datetime | None:
    at: dt.datetime | None = await session.scalar(
        sa.select(sa.func.max(GameEvent.created_at)).where(GameEvent.game_id == game.id)
    )
    if at is not None and at.tzinfo is None:
        return at.replace(tzinfo=dt.UTC)
    return at
