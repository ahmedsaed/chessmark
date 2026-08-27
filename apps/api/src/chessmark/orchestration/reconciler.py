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
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Player
from chessmark.db.repositories import append_event, finish_game, rebuild_referee
from chessmark.game import Colour, GameResult, Outcome, Termination
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue

log = logging.getLogger(__name__)

DEFAULT_STALE_AFTER = dt.timedelta(minutes=20)

#: How long a game may sit waiting for a person before it is written off (GAME-08).
#: Deliberately far longer than the stall threshold: a stalled *model* game is our fault and
#: should be rescued in minutes, while a person is entitled to leave the tab open over lunch.
DEFAULT_HUMAN_IDLE_AFTER = dt.timedelta(hours=2)


@dataclass(slots=True)
class ReconcileReport:
    requeued: list[str] = field(default_factory=list)
    abandoned: list[str] = field(default_factory=list)
    #: Waiting on a person, inside the idle window. Neither stalled nor abandoned.
    waiting: list[str] = field(default_factory=list)
    #: Paused for a provider rate limit, and put back on the queue because the wait is over.
    resumed: list[str] = field(default_factory=list)
    checked: int = 0

    def __str__(self) -> str:
        return (
            f"checked {self.checked} running games, requeued {len(self.requeued)}, "
            f"abandoned {len(self.abandoned)}, waiting on a person {len(self.waiting)}, "
            f"resumed {len(self.resumed)}"
        )


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
        sa.select(Game).where(
            Game.status == GameStatus.PAUSED,
            sa.or_(Game.resume_after.is_(None), Game.resume_after <= clock),
        )
    )
    return list(rows)


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


async def reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: TurnQueue,
    *,
    stale_after: dt.timedelta = DEFAULT_STALE_AFTER,
    human_idle_after: dt.timedelta = DEFAULT_HUMAN_IDLE_AFTER,
) -> ReconcileReport:
    report = ReconcileReport()
    jobs: list[AdvanceTurn] = []

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
        resumable = await find_resumable(session)
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
