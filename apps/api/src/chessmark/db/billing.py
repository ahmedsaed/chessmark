"""Reconciling a game against what OpenRouter billed for it, and settling its payer (ADR-0054).

**Checked at rest, not as it plays.** A game is reconciled once it has ended, or once it is held by
something that may never lift — its owner's pause, or its owner's credit running out. A provider's
pause is not a rest: it lifts on its own within minutes, and there were 1,468 of them in a month.

**Three checks, because OpenRouter's analytics lag.** About five minutes after the game comes to
rest, again after an hour, and once more after a day. Each check recomputes the total from scratch
and settles only what changed since the last, so a late generation is caught and nothing is counted
twice. A game that resumes and plays on has a new event cursor, and its schedule starts again.

**The billed total is exact.** Generations our record holds keep their recorded cost — OpenRouter's
own per-generation figure, to eight places. The ones it does not hold are priced one by one from
`GET /generation`, because the analytics total truncates each generation to six places.

**The payer is charged what OpenRouter billed**, the owner's decision: a lost round is a failure of
ours that should be rare, and the figure the account was actually charged is the honest one. Only a
game charged in dollars is settled (ADR-0052) — one from the credits era is recorded, not charged.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.core.openrouter_billing import OpenRouterBilling
from chessmark.db.credits import charged_for, settle
from chessmark.db.enums import CreditReason, GameStatus
from chessmark.db.models import CreditLedger, Game, LlmCall, UnrecordedGeneration

log = logging.getLogger(__name__)

#: When each check runs, after the game comes to rest.
CHECKS = (dt.timedelta(minutes=5), dt.timedelta(hours=1), dt.timedelta(days=1))

#: Games reconciled per sweep, and generations priced per sweep. Bounds on a minute's work, so a
#: backlog — every game at the first deploy — is worked through rather than done in one go.
GAMES_PER_SWEEP = 20
LOOKUPS_PER_SWEEP = 200

#: Below this a difference is rounding between our eight places and OpenRouter's nine.
SETTLE_THRESHOLD = Decimal("0.00000001")

#: The pause reasons that may never lift. Written by the worker; matched here by prefix.
OWNER_PAUSE = "paused by its owner"
CREDIT_PREFIX = "out of credit:"


@dataclass(slots=True)
class BillingReport:
    reconciled: list[str] = field(default_factory=list)
    settled_usd: Decimal = Decimal(0)
    unrecorded: int = 0

    def __str__(self) -> str:
        return (
            f"{len(self.reconciled)} game(s) reconciled, {self.unrecorded} unrecorded "
            f"generation(s) found, ${self.settled_usd} settled"
        )


def at_rest() -> sa.ColumnElement[bool]:
    return sa.or_(
        Game.status.in_((GameStatus.FINISHED, GameStatus.ABORTED)),
        sa.and_(
            Game.status == GameStatus.PAUSED,
            sa.or_(Game.pause_reason == OWNER_PAUSE, Game.pause_reason.like(f"{CREDIT_PREFIX}%")),
        ),
    )


def _due_now(now: dt.datetime) -> sa.ColumnElement[bool]:
    """Due for its next check, decided in SQL.

    **Not a limit followed by a filter.** Taking the first hundred games at rest and then asking
    which were due meant the hundred already checked once sat at the front of the list until their
    *second* check an hour later, and every game behind them waited: the first real run reached 100
    of 169. A game is a candidate only when a check is actually owed.
    """
    owed = [
        sa.and_(Game.billing_checks == index, Game.billing_anchor_at <= now - delay)
        for index, delay in enumerate(CHECKS)
    ]
    return sa.or_(
        Game.billing_anchor_at.is_(None),
        Game.billing_seq.is_distinct_from(Game.event_seq),
        *owed,
    )


async def due(session: AsyncSession, *, now: dt.datetime, limit: int) -> list[Game]:
    """Games at rest whose next check has come, oldest rest first. A game seen at rest for the first
    time, or again after it moved, starts its schedule here and is due five minutes later."""
    candidates = list(
        await session.scalars(
            sa.select(Game)
            .where(at_rest(), Game.created_at > now - dt.timedelta(days=30), _due_now(now))
            .order_by(Game.billing_anchor_at.asc().nulls_first(), Game.created_at)
            .limit(limit * 5)
        )
    )
    ready: list[Game] = []
    for game in candidates:
        if game.billing_seq != game.event_seq or game.billing_anchor_at is None:
            game.billing_anchor_at = now
            game.billing_seq = game.event_seq
            game.billing_checks = 0
            continue
        ready.append(game)
        if len(ready) >= limit:
            break
    return ready


async def _paid_in_dollars(session: AsyncSession, game: Game) -> bool:
    found = await session.scalar(
        sa.select(CreditLedger.id)
        .where(CreditLedger.game_id == game.id, CreditLedger.reason == CreditReason.TURN)
        .limit(1)
    )
    return found is not None


async def reconcile_game(
    session: AsyncSession,
    game: Game,
    client: OpenRouterBilling,
    *,
    now: dt.datetime,
    lookups: list[int],
    report: BillingReport,
) -> bool:
    """One check of one game. False when OpenRouter would not answer; the check is not spent."""
    billed = await client.generations(f"game-{game.id}", since=game.created_at, until=now)
    if billed is None:
        return False

    recorded: dict[str, Decimal] = {
        str(gid): Decimal(cost)
        for gid, cost in (
            await session.execute(
                sa.select(LlmCall.response["id"].astext, LlmCall.cost_usd).where(
                    LlmCall.game_id == game.id
                )
            )
        ).all()
        if gid
    }
    known: dict[str, Decimal] = {
        gid: Decimal(cost)
        for gid, cost in (
            await session.execute(
                sa.select(UnrecordedGeneration.generation_id, UnrecordedGeneration.cost_usd).where(
                    UnrecordedGeneration.game_id == game.id
                )
            )
        ).all()
    }

    unpriced = Decimal(0)
    for generation in billed:
        gid = generation.generation_id
        if gid in recorded or gid in known:
            continue
        cost: Decimal | None = Decimal(0) if generation.usage_floor == 0 else None
        if cost is None and lookups[0] > 0:
            lookups[0] -= 1
            cost = await client.cost_of(gid)
        if cost is None:
            # Not priced this sweep. Counted at its floor so the total is not short, and left
            # unstored so the next check prices it exactly.
            unpriced += generation.usage_floor
            continue
        session.add(UnrecordedGeneration(generation_id=gid, game_id=game.id, cost_usd=cost))
        known[gid] = cost
        report.unrecorded += 1

    total = sum(recorded.values(), Decimal(0)) + sum(known.values(), Decimal(0)) + unpriced
    game.billed_usd = total
    game.billed_requests = len(set(recorded) | set(known) | {g.generation_id for g in billed})
    game.billed_checked_at = now
    game.billing_checks += 1
    report.reconciled.append(str(game.id))

    payer = game.created_by_user_id
    if payer is not None and await _paid_in_dollars(session, game):
        difference = total - await charged_for(session, game.id)
        if abs(difference) >= SETTLE_THRESHOLD:
            await settle(session, payer, difference, game_id=game.id)
            report.settled_usd += difference
    return True


async def settle_billing(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: OpenRouterBilling,
    *,
    now: dt.datetime | None = None,
) -> BillingReport:
    """One sweep: every game due a check, each in its own transaction."""
    report = BillingReport()
    if not client.enabled:
        return report
    clock = now or dt.datetime.now(dt.UTC)
    lookups = [LOOKUPS_PER_SWEEP]

    async with sessionmaker() as session, session.begin():
        ids: list[uuid.UUID] = [
            game.id for game in await due(session, now=clock, limit=GAMES_PER_SWEEP)
        ]

    for game_id in ids:
        try:
            async with sessionmaker() as session, session.begin():
                game = await session.get(Game, game_id, with_for_update=True)
                if game is None:
                    continue
                await reconcile_game(
                    session, game, client, now=clock, lookups=lookups, report=report
                )
        except Exception:
            log.exception("reconciling %s failed", game_id)
    return report


__all__ = ["CHECKS", "BillingReport", "due", "reconcile_game", "settle_billing"]
