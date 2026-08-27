"""Running a tournament: the loop that keeps games in flight until the event is done.

Deliberately small, and deliberately stateless between calls. `advance` looks at what the database
says has been played, decides what should happen next, and does one step of it. Everything it needs
to know is written down, so a crash costs at most the game that was in flight — the caller can be a
cron tick, a supervised daemon, or a person running it by hand, and none of them holds state the
others would miss.

That is what the "resumes without replaying completed games" criterion actually requires: not
recovery logic, but never having depended on process memory in the first place.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.bench.service import compute_ratings
from chessmark.core.budget import FreeTierBudget
from chessmark.core.cooldown import ProviderCooldown
from chessmark.db import tournaments as repo
from chessmark.db.enums import TournamentStatus
from chessmark.db.models import (
    Game,
    ModelEndpoint,
    ModelRegistry,
    Tournament,
    TournamentEntrant,
    TournamentGame,
)
from chessmark.orchestration.match import Seat, create_match, start_match
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue
from chessmark.tournament import Form, Format, matchmake, round_robin, swiss_round

log = logging.getLogger(__name__)

#: How many paused games a pool tolerates beyond its concurrency before it stops starting new ones.
#: Small on purpose: the headroom exists so one unlucky pause does not stall an otherwise healthy
#: pool, not so a hot provider can be absorbed by opening more games.
MAX_PAUSED_HEADROOM = 2


def within_window(active_from: dt.time | None, active_until: dt.time | None, now: dt.time) -> bool:
    """Whether the clock is inside an event's active hours.

    Compared rather than subtracted so a window that wraps midnight works: 22:00 to 04:00 means
    "late evening through the small hours", not an empty range.
    """
    if active_from is None or active_until is None:
        return True
    if active_from <= active_until:
        return active_from <= now < active_until
    return now >= active_from or now < active_until


@dataclass(frozen=True, slots=True)
class Step:
    """What one call to `advance` did. Enough for a log line or a status endpoint."""

    started: int = 0
    settled: int = 0
    scheduled_round: int | None = None
    #: Models a pool admitted this tick, having appeared in the catalogue since the last one.
    admitted: tuple[str, ...] = ()
    #: Why nothing started, when something otherwise would have — outside its hours, or out of
    #: free-tier allowance. Reported so a quiet tick is explicable rather than mysterious.
    holding: str = ""
    status: TournamentStatus = TournamentStatus.RUNNING
    detail: str = ""

    @property
    def idle(self) -> bool:
        return not (self.started or self.settled or self.scheduled_round or self.admitted)


async def advance(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: TurnQueue,
    *,
    tournament_id: uuid.UUID,
    free_tier: FreeTierBudget | None = None,
    cooldown: ProviderCooldown | None = None,
    now: dt.datetime | None = None,
) -> Step:
    """Do one step: settle what finished, schedule what is next, start what fits.

    The order matters. Settling first frees concurrency and updates the standings a Swiss round
    would pair from; scheduling before starting means the schedule is on disk before any game
    exists, which is what makes the event resumable.
    """
    async with sessionmaker() as session:
        tournament = await session.get(Tournament, tournament_id)
        if tournament is None:
            raise LookupError(f"no tournament {tournament_id}")

        if tournament.status in {TournamentStatus.FINISHED, TournamentStatus.ABANDONED}:
            return Step(status=tournament.status, detail="already over")

        # A paused event stays paused until somebody says otherwise. Without this it would
        # restart on the next tick — including one paused by its own budget, which would then
        # spend straight past the ceiling it had just stopped at.
        if tournament.status is TournamentStatus.PAUSED:
            return Step(status=tournament.status, detail="paused; resume to continue")

        settled = await _settle_finished(session, tournament)

        admitted: list[str] = []
        if tournament.format == str(Format.POOL):
            # Before pairing, not after: a model listed this morning should be eligible for the
            # game chosen this afternoon, and the matchmaker prioritises whoever is least known.
            admitted = await repo.admit_new_entrants(
                session, tournament, repo.filter_from_json(tournament.field_filter)
            )

        over_budget = await _budget_reached(session, tournament)
        if over_budget is not None:
            tournament.status = TournamentStatus.PAUSED
            tournament.ended_at = sa.func.now()
            await session.commit()
            return Step(settled=settled, status=TournamentStatus.PAUSED, detail=over_budget)

        scheduled = await _schedule_next_round(session, tournament, cooldown)

        # A pool has no end to reach.
        if tournament.format != str(Format.POOL) and await _is_complete(session, tournament):
            tournament.status = TournamentStatus.FINISHED
            tournament.ended_at = sa.func.now()
            await session.commit()
            return Step(
                settled=settled, status=TournamentStatus.FINISHED, detail="every round played"
            )

        holding = await _holding(session, tournament, free_tier, now)
        if holding:
            await session.commit()
            return Step(
                settled=settled,
                admitted=tuple(admitted),
                scheduled_round=scheduled,
                detail=holding,
                holding=holding,
            )

        started, jobs = await _start_games(session, queue, tournament)

        if tournament.status is TournamentStatus.PENDING and started:
            tournament.status = TournamentStatus.RUNNING
            tournament.started_at = sa.func.now()

        await session.commit()

    # Enqueued only after the transaction commits, so a job never names a game that does not
    # exist — the same ordering the rest of the orchestration keeps (ADR-0007).
    for job in jobs:
        await queue.enqueue(job)

    return Step(
        started=started, settled=settled, scheduled_round=scheduled, admitted=tuple(admitted)
    )


async def _holding(
    session: AsyncSession,
    tournament: Tournament,
    free_tier: FreeTierBudget | None,
    now: dt.datetime | None,
) -> str:
    """Why no new game may start right now, or an empty string if one may.

    Checked before starting rather than after, like every other bound here: a game begun outside
    its window, or on an allowance that has run out, cannot be un-begun.
    """
    clock = (now or dt.datetime.now(dt.UTC)).time()
    if not within_window(tournament.active_from, tournament.active_until, clock):
        return (
            f"outside its active hours "
            f"({tournament.active_from:%H:%M}-{tournament.active_until:%H:%M} UTC)"
        )

    if tournament.max_games_per_day is not None:
        since = (now or dt.datetime.now(dt.UTC)).date()
        started_today = await session.scalar(
            sa.select(sa.func.count(TournamentGame.id)).where(
                TournamentGame.tournament_id == tournament.id,
                TournamentGame.started_at >= since,
            )
        )
        if int(started_today or 0) >= tournament.max_games_per_day:
            return f"its daily cap of {tournament.max_games_per_day} games is reached"

    # The free tier is limited by a request count nobody reports back to us, so the only way to
    # stay under it is to count our own calls and stop early. Account-wide: every pool, every
    # human game against a free model and every `make play` draw on the same allowance.
    if (
        free_tier is not None
        and await _uses_free_models(session, tournament)
        and await free_tier.tripped()
    ):
        used = await free_tier.used_today()
        return f"the free-tier allowance is spent ({used} of {free_tier.usable} usable today)"

    return ""


async def _uses_free_models(session: AsyncSession, tournament: Tournament) -> bool:
    """Whether any entrant is a free variant, and so draws on the shared daily allowance."""
    found = await session.scalar(
        sa.select(TournamentEntrant.id)
        .join(ModelRegistry, ModelRegistry.id == TournamentEntrant.model_id)
        .where(
            TournamentEntrant.tournament_id == tournament.id,
            TournamentEntrant.withdrawn.is_(False),
            ModelRegistry.is_free.is_(True),
        )
        .limit(1)
    )
    return found is not None


async def _settle_finished(session: AsyncSession, tournament: Tournament) -> int:
    """Record results for pairings whose game has ended."""
    rows = list(
        await session.scalars(
            sa.select(TournamentGame).where(
                TournamentGame.tournament_id == tournament.id,
                TournamentGame.game_id.is_not(None),
                TournamentGame.white_score.is_(None),
                TournamentGame.abandoned_reason.is_(None),
            )
        )
    )

    settled = 0
    for row in rows:
        game = await session.get(Game, row.game_id)
        if game is not None and await repo.settle(session, row, game):
            settled += 1
    return settled


async def _budget_reached(session: AsyncSession, tournament: Tournament) -> str | None:
    """Whether the event has spent its ceiling. Checked before starting anything, never after.

    Noticing afterwards means the money is already gone — the same reasoning as the per-game cap
    in the worker (ADR-0011, layer 3).
    """
    if tournament.max_usd is None:
        return None

    used = await repo.spent(session, tournament.id)
    tournament.total_cost_usd = used
    if used < Decimal(tournament.max_usd):
        return None
    return f"Stopped after ${used} of a ${tournament.max_usd} budget."


async def _schedule_next_round(
    session: AsyncSession, tournament: Tournament, cooldown: ProviderCooldown | None = None
) -> int | None:
    """Write the next round's pairings down, if the current one is done.

    Round robin is scheduled in full the first time, because it can be: the whole fixture list is
    known from the field. Swiss cannot — round two depends on round one — so it is written one
    round at a time, which is also why a Swiss event resumes correctly: the next pairing is
    *derived* from the results, so a crash cannot desynchronise it.
    """
    entrants = await repo.entrants_of(session, tournament.id)
    if len(entrants) < 2:
        return None

    played = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.max(TournamentGame.round_number), 0)).where(
            TournamentGame.tournament_id == tournament.id
        )
    )
    highest = int(played or 0)

    if tournament.format == str(Format.POOL):
        return await _schedule_pool(session, tournament, entrants, cooldown)

    if tournament.format == str(Format.ROUND_ROBIN):
        if highest:
            return None
        for games in round_robin(entrants, double=tournament.double):
            await repo.record_round(session, tournament.id, games)
        return 1

    if highest >= tournament.rounds:
        return None

    # A Swiss round may only be paired once the previous one has finished, or its pairings would
    # be drawn from half a standings table.
    if highest and (
        await repo.unplayed(session, tournament.id, round_number=highest)
        or await repo.in_flight(session, tournament.id)
    ):
        return None

    results = await repo.results_so_far(session, tournament.id)
    await repo.record_round(session, tournament.id, swiss_round(entrants, results, highest + 1))
    return highest + 1


async def _resting_entrants(
    session: AsyncSession, cooldown: ProviderCooldown, entrants: list[Any]
) -> set[str]:
    """Which entrants cannot be played right now.

    Two reasons, and the second is the one production taught us:

    * the model's own endpoint is resting off a refusal;
    * **every endpoint it has belongs to a provider resting a shared pool.** `gemma-4-26b` was
      cooled down and correctly skipped, so the matchmaker paired `gemma-4-31b` — a different model
      on the same hot Google AI Studio pool — which paused a minute later for the same reason.

    "Every endpoint" and not "any": a paid model served by nineteen providers is not unavailable
    because one of them is resting, and the router would simply pick another. A free model has
    exactly one, so for the pool that caused this the two readings coincide.
    """
    slugs = [e.key.split("@", 1)[0] for e in entrants]
    by_model = await cooldown.resting(slugs)

    providers = await cooldown.resting_providers()
    if providers:
        rows = (
            await session.execute(
                sa.select(ModelRegistry.openrouter_id, ModelEndpoint.provider_name)
                .join(ModelEndpoint, ModelEndpoint.model_id == ModelRegistry.id)
                .where(
                    ModelRegistry.openrouter_id.in_(slugs),
                    ModelEndpoint.is_active.is_(True),
                    ModelEndpoint.supports_tools.is_(True),
                )
            )
        ).all()
        serves: dict[str, set[str]] = {}
        for slug, provider in rows:
            serves.setdefault(slug, set()).add(provider)
        by_model |= {slug for slug, on in serves.items() if on and on.issubset(providers)}

    return {e.key for e in entrants if e.key.split("@", 1)[0] in by_model}


async def _schedule_pool(
    session: AsyncSession,
    tournament: Tournament,
    entrants: list[Any],
    cooldown: ProviderCooldown | None = None,
) -> int | None:
    """Pair as many games as there is room to run, and no more.

    A pool never runs out of fixtures, so scheduling ahead would write a queue nobody asked for and
    freeze the matchmaker's information at the moment it was written. Pairing only what can start
    now means every choice is made with the latest ratings — including for a model admitted on
    this same tick.
    """
    waiting = len(await repo.unplayed(session, tournament.id))
    running = len(await repo.in_flight(session, tournament.id))
    room = tournament.max_concurrent - waiting - running
    if room <= 0:
        return None

    # **A ceiling on paused games**, which `in_flight` deliberately does not count. Freeing the
    # slot is right — a paused game is not spending — but with nothing bounding the *inactive* pile
    # a broadly hot free tier turns every pairing into a paused game in turn: observed as four
    # paused games against a concurrency of one, marching through the field. Each would then wake,
    # be refused again, and eventually abandon, which is the original failure at a slower tempo.
    #
    # Holding instead means the pool waits for the provider rather than spending the field on it.
    paused = await repo.paused(session, tournament.id)
    if len(paused) >= tournament.max_concurrent + MAX_PAUSED_HEADROOM:
        log.info(
            "pool %s holding: %d games are paused, waiting for them rather than starting more",
            tournament.slug,
            len(paused),
        )
        return None

    highest = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.max(TournamentGame.round_number), 0)).where(
            TournamentGame.tournament_id == tournament.id
        )
    )
    round_number = int(highest or 0) + 1

    # Whoever cannot play right now is skipped rather than paired. This is the difference between
    # a pool that spends its one concurrency slot on a model that can move and one that spends
    # ninety minutes rediscovering that a provider is rate-limited — see `core/cooldown.py`.
    resting: set[str] = set()
    if cooldown is not None:
        resting = await _resting_entrants(session, cooldown, entrants)
        if resting:
            log.info("pool %s skipping %d resting entrants", tournament.slug, len(resting))

    games = matchmake(
        entrants,
        await repo.results_so_far(session, tournament.id),
        await _form(session, tournament),
        count=room,
        round_number=round_number,
        unavailable=resting,
    )
    if not games:
        return None

    await repo.record_round(session, tournament.id, games)
    return round_number


async def _form(session: AsyncSession, tournament: Tournament) -> dict[str, Form]:
    """What is known about each entrant, for the matchmaker.

    Ratings come from the leaderboard over *ranked* games (BENCH-03) rather than from this pool's
    own results: a model that arrives already rated is not unknown, and pairing it as though it
    were would spend games rediscovering what is already measured.
    """
    run = await compute_ratings(session)

    # A contestant is `(model, quantization)`, but a pool's entrants are usually keyed by slug
    # alone — the precision is decided per game by the router. Both are looked up, so a pool that
    # does pin one still finds its rating.
    by_key: dict[str, Any] = {}
    by_slug: dict[str, Any] = {}
    for contestant, rating in run.ratings.items():
        by_key[f"{contestant.model_slug}@{contestant.quantization}"] = rating
        # If a model is served at several precisions, the least certain of them stands in: it is
        # the one a game would tell us most about.
        current = by_slug.get(contestant.model_slug)
        if current is None or rating.rd > current.rd:
            by_slug[contestant.model_slug] = rating

    form: dict[str, Form] = {}
    for entrant in await repo.entrants_of(session, tournament.id):
        known = by_key.get(entrant.key) or by_slug.get(entrant.key.split("@", 1)[0])
        if known is not None:
            form[entrant.key] = Form(
                key=entrant.key,
                rating=float(known.rating),
                deviation=float(known.rd),
            )
    return form


async def _is_complete(session: AsyncSession, tournament: Tournament) -> bool:
    """Every scheduled pairing settled, and no round left to schedule."""
    if await repo.unplayed(session, tournament.id) or await repo.in_flight(session, tournament.id):
        return False

    scheduled = await session.scalar(
        sa.select(sa.func.count(TournamentGame.id)).where(
            TournamentGame.tournament_id == tournament.id
        )
    )
    if not scheduled:
        return False

    if tournament.format == str(Format.SWISS):
        highest = await session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(TournamentGame.round_number), 0)).where(
                TournamentGame.tournament_id == tournament.id
            )
        )
        return int(highest or 0) >= tournament.rounds
    return True


async def _start_games(
    session: AsyncSession, queue: TurnQueue, tournament: Tournament
) -> tuple[int, list[AdvanceTurn]]:
    """Create and start as many games as the concurrency bound allows."""
    running = len(await repo.in_flight(session, tournament.id))
    room = tournament.max_concurrent - running
    if room <= 0:
        return 0, []

    waiting = await repo.unplayed(session, tournament.id)
    if not waiting:
        return 0, []

    entrants = {e.key: e for e in await repo.entrants_of(session, tournament.id)}
    jobs: list[AdvanceTurn] = []
    started = 0

    for row in waiting[:room]:
        if row.black_key is None:  # a bye needs no game
            continue
        white, black = entrants.get(row.white_key), entrants.get(row.black_key)
        if white is None or black is None:
            row.abandoned_reason = "an entrant withdrew before this pairing was played"
            continue

        match = await create_match(
            session,
            white=await _seat(session, row.white_key, white.label),
            black=await _seat(session, row.black_key, black.label),
            is_ranked=tournament.is_ranked,
            max_plies=tournament.max_plies_per_game,
            max_usd=tournament.max_usd_per_game,
        )
        job = await start_match(session, queue, game_id=match.game.id)
        row.game_id = match.game.id
        row.started_at = sa.func.now()
        jobs.append(job)
        started += 1

    await session.flush()
    return started, jobs


async def _seat(session: AsyncSession, key: str, label: str) -> Seat:
    """One side of a pairing, resolved back to a registry row."""
    model_slug, _, quantization = key.partition("@")
    model_id = await session.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == model_slug)
    )
    return Seat(
        display_name=label or model_slug,
        model=model_slug,
        model_id=model_id,
        quantization=quantization or None,
    )
