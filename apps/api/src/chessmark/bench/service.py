"""Turning finished games into ratings and aggregate metrics.

The database half of Phase 12: `bench.glicko2` does the arithmetic and `bench.ratable` decides what
counts, both without touching a session. This joins them to the tables.

**Recomputed from scratch every time, never updated in place.** Ratings are a pure function of the
games that produced them, and a stored value that drifted from that function would be undetectable
— which is exactly the property the determinism criterion is about. Rebuilding a few hundred games
costs milliseconds; being unable to trust the number costs the whole leaderboard.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.bench.glicko2 import Glicko2, Outcome
from chessmark.bench.glicko2 import Rating as Glicko2Rating
from chessmark.bench.ratable import GameFacts, judge
from chessmark.db.enums import GameStatus
from chessmark.db.models import (
    Game,
    LlmCall,
    ModelEndpoint,
    ModelRegistry,
    Player,
    Rating,
    TournamentGame,
    Turn,
)
from chessmark.game import Colour, GameResult, Termination

#: One rating period per calendar day, UTC. Glicko-2 is defined over batches, and a period short
#: enough to hold a single game defeats the point — the deviation would never settle.
PERIOD_EPOCH = dt.date(2026, 1, 1)


def period_of(when: dt.datetime) -> int:
    return (when.astimezone(dt.UTC).date() - PERIOD_EPOCH).days


@dataclass(frozen=True, slots=True)
class Contestant:
    """`(model, quantization)` — the thing that is rated (ADR-0015)."""

    model_id: uuid.UUID
    model_slug: str
    quantization: str

    @property
    def label(self) -> str:
        return f"{self.model_slug}@{self.quantization}"


@dataclass(slots=True)
class Excluded:
    """A finished game that did not count, and the sentence explaining it.

    Collected rather than discarded so the methodology page can show its work. "Some games are
    excluded" invites disbelief; a table of game ids and reasons does not (BENCH-10).
    """

    game_id: uuid.UUID
    reason: str


@dataclass(slots=True)
class RatingRun:
    ratings: dict[Contestant, Glicko2Rating] = field(default_factory=dict)
    games_counted: int = 0
    excluded: list[Excluded] = field(default_factory=list)
    periods: list[int] = field(default_factory=list)


@dataclass(slots=True)
class Aggregate:
    """Per-contestant metrics that are facts rather than inferences (BENCH-02)."""

    contestant: Contestant
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    illegal_attempts: int = 0
    moves_played: int = 0
    forfeits: int = 0
    total_cost_usd: Decimal = Decimal(0)
    latency_ms_total: int = 0
    llm_calls: int = 0

    @property
    def illegal_per_move(self) -> float:
        """The benchmark's headline number, and the reason the project exists."""
        return self.illegal_attempts / self.moves_played if self.moves_played else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return self.latency_ms_total / self.llm_calls if self.llm_calls else 0.0

    @property
    def mean_cost_usd(self) -> Decimal:
        return self.total_cost_usd / self.games if self.games else Decimal(0)


#: The statuses a game must have reached before it can be judged at all.
TERMINAL = (GameStatus.FINISHED, GameStatus.ABORTED)


@dataclass(slots=True)
class Scan:
    """One batched read of every eligible game, its seats, and their precisions.

    Every eligibility decision the leaderboard makes comes from here, and it is deliberately a
    handful of set-based queries rather than a loop. The first version read per game — seats, then
    providers, then a precision lookup per seat — which is four round trips a game before any
    arithmetic happens, and the callers then ran the whole loop again: `compute_ratings` scanned
    twice (`ratable_games` and `excluded_games`), the route scanned a third time for the
    aggregates, and `ratings_by_key` a fourth. Thirty-seven games cost 295 queries and a fifth of
    a second, on the critical path of four pages.

    Scanning once and grouping in Python is the same answer in seven queries, and it stays seven.
    """

    counted: list[tuple[Game, list[Player], dict[uuid.UUID, str]]] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)


def _finished_ids(tournament_id: uuid.UUID | None) -> sa.Select[tuple[uuid.UUID]]:
    """The ids `_finished` selects, as a subquery the batched reads can filter on.

    Same `where` clause, same scope rule (ADR-0027) — expressed as ids so every query below can
    narrow itself without repeating the join.
    """
    query = sa.select(Game.id).where(Game.status.in_(TERMINAL))
    if tournament_id is not None:
        query = query.join(TournamentGame, TournamentGame.game_id == Game.id).where(
            TournamentGame.tournament_id == tournament_id
        )
    return query


async def _seats(
    session: AsyncSession, tournament_id: uuid.UUID | None
) -> dict[uuid.UUID, list[Player]]:
    """Every seat of every eligible game, grouped by game.

    Ordered by colour so a rebuild is byte-identical: the seat order feeds the `pinned`/`used`
    tuples `judge` zips, and an unordered read made "recomputing reproduces the same ratings"
    true only by luck.
    """
    seats: dict[uuid.UUID, list[Player]] = {}
    rows = await session.scalars(
        sa.select(Player)
        .where(Player.game_id.in_(_finished_ids(tournament_id)))
        .order_by(Player.game_id, Player.colour)
    )
    for player in rows:
        seats.setdefault(player.game_id, []).append(player)
    return seats


async def _providers_used(
    session: AsyncSession, tournament_id: uuid.UUID | None
) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[str, ...]]:
    """Which endpoints actually served each seat, keyed by `(game, player)`.

    A seat served by more than one is what excludes a game (ADR-0015), so this is the fact the
    whole eligibility rule turns on.
    """
    used: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, ...]] = {}
    rows = await session.execute(
        sa.select(Turn.game_id, Turn.player_id, LlmCall.provider)
        .join(LlmCall, LlmCall.turn_id == Turn.id)
        .where(Turn.game_id.in_(_finished_ids(tournament_id)), LlmCall.provider.is_not(None))
        .distinct()
        .order_by(Turn.game_id, Turn.player_id, LlmCall.provider)
    )
    for game_id, player_id, provider in rows:
        key = (game_id, player_id)
        used[key] = (*used.get(key, ()), str(provider))
    return used


async def _served_quantization(
    session: AsyncSession, tournament_id: uuid.UUID | None
) -> dict[tuple[uuid.UUID, uuid.UUID], str]:
    """The precision that actually served each seat, for games played before pinning existed."""
    rows = await session.execute(
        sa.select(Turn.game_id, Turn.player_id, ModelEndpoint.quantization)
        .join(LlmCall, LlmCall.turn_id == Turn.id)
        .join(ModelRegistry, ModelRegistry.openrouter_id == LlmCall.model_slug)
        .join(
            ModelEndpoint,
            sa.and_(
                ModelEndpoint.model_id == ModelRegistry.id,
                ModelEndpoint.provider_name == LlmCall.provider,
            ),
        )
        .where(Turn.game_id.in_(_finished_ids(tournament_id)))
        .distinct()
        .order_by(Turn.game_id, Turn.player_id, ModelEndpoint.quantization)
    )
    return {
        (game_id, player_id): (quantization or "unknown")
        for game_id, player_id, quantization in rows
    }


async def _endpoint_quantization(session: AsyncSession) -> dict[tuple[uuid.UUID, str], str]:
    """`(model, provider) -> precision` for the whole endpoint table.

    Small enough to read whole — a few hundred rows — and reading it whole replaces one query per
    seat per game.
    """
    rows = await session.execute(
        sa.select(ModelEndpoint.model_id, ModelEndpoint.provider_name, ModelEndpoint.quantization)
    )
    return {
        (model_id, provider_name): quantization
        for model_id, provider_name, quantization in rows
        if quantization is not None
    }


async def scan(
    session: AsyncSession,
    *,
    prompt_version: str | None = PROMPT_VERSION,
    tournament_id: uuid.UUID | None = None,
) -> Scan:
    """Judge every eligible game in one pass, batching every read.

    Returns the games that count *and* the ones that did not with their reasons, because both come
    from the same verdict and computing them separately is how they drift apart.
    """
    games = list(await session.scalars(_finished(tournament_id)))
    if not games:
        return Scan()

    seats = await _seats(session, tournament_id)
    used = await _providers_used(session, tournament_id)
    served = await _served_quantization(session, tournament_id)
    endpoints = await _endpoint_quantization(session)

    result = Scan()
    for game in games:
        players = seats.get(game.id, [])
        facts = GameFacts(
            is_ranked=game.is_ranked,
            termination=game.termination,
            prompt_version=game.prompt_version,
            pinned_providers=tuple(_pinned(p) for p in players),
            used_providers=tuple(used.get((game.id, p.id), ()) for p in players),
            model_slugs=tuple(str((p.sampling or {}).get("model") or "") for p in players),
            trash_talk_enabled=game.trash_talk_enabled,
        )

        verdict = judge(facts, prompt_version=prompt_version)
        if not verdict:
            result.excluded.append(Excluded(game_id=game.id, reason=verdict.reason))
            continue

        result.counted.append((game, players, _quantizations(game.id, players, served, endpoints)))

    return result


def _quantizations(
    game_id: uuid.UUID,
    players: list[Player],
    served: dict[tuple[uuid.UUID, uuid.UUID], str],
    endpoints: dict[tuple[uuid.UUID, str], str],
) -> dict[uuid.UUID, str]:
    """The precision each seat played at.

    From the **pinned** endpoint first, because that is where the contestant's identity is decided —
    at match creation, before a single call is made (ADR-0015). Inferring it afterwards from
    `llm_calls` is strictly worse: a game that made no calls, or whose provider row has since been
    renamed, silently becomes `unknown` and lands in the wrong leaderboard row.

    Falls back to what actually served, for games played before pinning existed.
    """
    quantizations: dict[uuid.UUID, str] = {}
    for player in players:
        provider = _pinned(player)
        pinned = (
            endpoints.get((player.model_id, provider))
            if provider is not None and player.model_id is not None
            else None
        )
        quantizations[player.id] = pinned or served.get((game_id, player.id)) or "unknown"
    return quantizations


def _pinned(player: Player) -> str | None:
    only = (player.provider_routing or {}).get("only") or []
    return str(only[0]) if only else None


def _score(result: GameResult, colour: Colour) -> float:
    if result is GameResult.DRAW:
        return 0.5
    if result is GameResult.WHITE_WINS:
        return 1.0 if colour is Colour.WHITE else 0.0
    return 1.0 if colour is Colour.BLACK else 0.0


def _contestant(player: Player, quantizations: dict[uuid.UUID, str]) -> Contestant | None:
    if player.model_id is None:
        return None
    slug = str((player.sampling or {}).get("model") or "")
    if not slug:
        return None
    return Contestant(
        model_id=player.model_id,
        model_slug=slug,
        quantization=quantizations.get(player.id, "unknown"),
    )


def _finished(tournament_id: uuid.UUID | None) -> sa.Select[tuple[Game]]:
    """Games that have ended, optionally only one event's.

    The scope exists so a pool's own table can be ordered by a rating computed over that pool's
    games alone (ADR-0027). It is a `where` clause and nothing else: **the eligibility rules do not
    change with it**, so a game the leaderboard excluded is excluded from the pool table too, and
    the two can never disagree about which games count.
    """
    query = sa.select(Game).where(Game.status.in_([GameStatus.FINISHED, GameStatus.ABORTED]))
    if tournament_id is not None:
        query = query.join(TournamentGame, TournamentGame.game_id == Game.id).where(
            TournamentGame.tournament_id == tournament_id
        )
    return query.order_by(Game.created_at)


async def ratable_games(
    session: AsyncSession,
    *,
    prompt_version: str | None = PROMPT_VERSION,
    tournament_id: uuid.UUID | None = None,
) -> list[tuple[Game, list[Player], dict[uuid.UUID, str]]]:
    """Every game that may move a rating, with its seats and their precisions.

    One eligibility decision shared by the ratings, the aggregates, and the drill-down. Three call
    sites deciding it separately is three chances for a leaderboard whose rating, whose
    illegal-move rate and whose "games behind this row" cover different sets of games.
    """
    scanned = await scan(session, prompt_version=prompt_version, tournament_id=tournament_id)
    return scanned.counted


async def compute_ratings(
    session: AsyncSession,
    *,
    prompt_version: str | None = PROMPT_VERSION,
    tau: float = 0.5,
    tournament_id: uuid.UUID | None = None,
    scanned: Scan | None = None,
) -> RatingRun:
    """Rebuild every rating from every eligible game.

    Games are grouped into periods and each period is rated as a batch — Glicko-2 is defined that
    way, and rating game by game gives a different and less defensible answer.

    `tournament_id` narrows the games to one event, which is what a pool's own table is ordered by.
    Everything else is identical — the same eligibility, the same engine, the same daily periods —
    so the two numbers differ only in what they were computed over, which is the whole point of
    having a local one (ADR-0027).

    `scanned` lets a caller that also wants the aggregates pay for the read once. Omitting it reads
    afresh, which is what every test and script does.
    """
    system = Glicko2(tau=tau)
    run = RatingRun()

    if scanned is None:
        scanned = await scan(session, prompt_version=prompt_version, tournament_id=tournament_id)

    # The exclusions come from the pass that produced the inclusions. Computing them separately was
    # a second identical sweep of every game, and two sweeps are two chances to disagree.
    run.excluded = list(scanned.excluded)

    by_period: dict[int, list[tuple[Game, list[Player], dict[uuid.UUID, str]]]] = {}
    for game, players, quantizations in scanned.counted:
        by_period.setdefault(period_of(game.ended_at or game.created_at), []).append(
            (game, players, quantizations)
        )
        run.games_counted += 1

    run.periods = sorted(by_period)

    for period in run.periods:
        # Every contestant seen so far is rated for this period, including those who did not play:
        # an idle period must widen the deviation, or a stale rating keeps its confidence forever.
        outcomes: dict[Contestant, list[Outcome]] = {c: [] for c in run.ratings}

        for game, players, quantizations in by_period[period]:
            seats: list[tuple[Contestant, Player]] = []
            for player in players:
                contestant = _contestant(player, quantizations)
                if contestant is not None:
                    seats.append((contestant, player))

            if len(seats) != 2:
                run.excluded.append(
                    Excluded(game.id, "a seat could not be resolved to a contestant")
                )
                run.games_counted -= 1
                continue

            for (contestant, player), (other, _) in (seats, seats[::-1]):
                outcomes.setdefault(contestant, [])
                outcomes[contestant].append(
                    Outcome(
                        opponent=run.ratings.get(other, Glicko2Rating()),
                        score=_score(game.result, player.colour),
                    )
                )

        for contestant, contest_outcomes in outcomes.items():
            run.ratings[contestant] = system.rate(
                run.ratings.get(contestant, Glicko2Rating()), contest_outcomes
            )

    return run


async def ratings_by_key(
    session: AsyncSession,
    *,
    tournament_id: uuid.UUID,
    prompt_version: str | None = PROMPT_VERSION,
) -> dict[str, tuple[float, float, bool]]:
    """One event's rating, deviation and provisional flag, keyed the way a tournament keys its
    entrants (ADR-0027).

    The leaderboard is keyed by `Contestant` — model **and quantization**, because that is what is
    actually being rated (ADR-0015). A tournament's entrants are keyed by model slug alone, and the
    table has to join to something. Within one event the two almost always coincide: a seat is
    pinned at creation, so a model plays every game of a pool on one endpoint at one precision.

    Almost. A registry change mid-event can leave one model with two contestants, and then a slug
    has two ratings and neither is wrong. The one with more games wins, because it is the one the
    reader is looking at — and the alternative, averaging them, would invent a number no game
    produced.
    """
    # One scan for both halves: the rating, and the game counts that break a slug's ties.
    scanned = await scan(session, prompt_version=prompt_version, tournament_id=tournament_id)
    run = await compute_ratings(
        session, prompt_version=prompt_version, tournament_id=tournament_id, scanned=scanned
    )

    played: dict[Contestant, int] = {}
    for _game, players, quantizations in scanned.counted:
        for player in players:
            contestant = _contestant(player, quantizations)
            if contestant is not None:
                played[contestant] = played.get(contestant, 0) + 1

    best: dict[str, tuple[float, float, bool]] = {}
    chosen: dict[str, int] = {}
    for contestant, rating in run.ratings.items():
        games = played.get(contestant, 0)
        if contestant.model_slug not in best or games > chosen[contestant.model_slug]:
            chosen[contestant.model_slug] = games
            best[contestant.model_slug] = (rating.rating, rating.rd, rating.provisional)

    return best


async def excluded_games(
    session: AsyncSession,
    *,
    prompt_version: str | None = PROMPT_VERSION,
    tournament_id: uuid.UUID | None = None,
) -> list[Excluded]:
    """Finished games that did not count, with the sentence explaining each.

    Reported rather than discarded. "Some games are excluded" invites disbelief; a list of ids and
    reasons is checkable (BENCH-10).
    """
    scanned = await scan(session, prompt_version=prompt_version, tournament_id=tournament_id)
    return scanned.excluded


async def store_ratings(session: AsyncSession, run: RatingRun) -> int:
    """Replace the stored ratings with a freshly computed set.

    A wholesale replace, not an upsert: the run *is* the answer, and leaving a row behind for a
    contestant that no longer qualifies would be a rating nothing supports.
    """
    await session.execute(sa.delete(Rating))

    period = run.periods[-1] if run.periods else 0
    for contestant, rating in run.ratings.items():
        session.add(
            Rating(
                model_id=contestant.model_id,
                quantization=contestant.quantization,
                period=period,
                rating=rating.rating,
                rating_deviation=rating.rd,
                volatility=rating.volatility,
                games_played=0,
            )
        )
    await session.flush()
    return len(run.ratings)


async def compute_aggregates(
    session: AsyncSession,
    *,
    prompt_version: str | None = PROMPT_VERSION,
    scanned: Scan | None = None,
) -> dict[Contestant, Aggregate]:
    """Per-contestant metrics over the same games the ratings used.

    The *same* eligibility scan, deliberately: a leaderboard whose rating and whose illegal-move
    rate covered different sets of games would be quietly incoherent.

    The two counts a seat needs — moves played, and total latency across its calls — are read for
    every seat at once. Reading them per seat is two round trips per player per game, and it was
    most of what made this function's cost grow with the archive.
    """
    if scanned is None:
        scanned = await scan(session, prompt_version=prompt_version)

    seat_ids = [player.id for _, players, _ in scanned.counted for player in players]
    moves_by_player = await _moves_played(session, seat_ids)
    calls_by_player = await _call_totals(session, seat_ids)

    aggregates: dict[Contestant, Aggregate] = {}

    for game, players, quantizations in scanned.counted:
        for player in players:
            contestant = _contestant(player, quantizations)
            if contestant is None:
                continue

            entry = aggregates.setdefault(contestant, Aggregate(contestant=contestant))
            entry.games += 1
            entry.illegal_attempts += player.illegal_attempts
            entry.total_cost_usd += player.total_cost_usd
            if player.forfeited:
                entry.forfeits += 1

            score = _score(game.result, player.colour)
            if score == 1.0:
                entry.wins += 1
            elif score == 0.5:
                entry.draws += 1
            else:
                entry.losses += 1

            entry.moves_played += moves_by_player.get(player.id, 0)
            total_latency, calls = calls_by_player.get(player.id, (0, 0))
            entry.latency_ms_total += total_latency
            entry.llm_calls += calls

    return aggregates


async def _moves_played(session: AsyncSession, player_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Turns that produced a ply, per seat — the denominator of the illegal-move rate."""
    if not player_ids:
        return {}
    rows = await session.execute(
        sa.select(Turn.player_id, sa.func.count())
        .where(Turn.player_id.in_(player_ids), Turn.ply_number.is_not(None))
        .group_by(Turn.player_id)
    )
    return {player_id: int(count) for player_id, count in rows}


async def _call_totals(
    session: AsyncSession, player_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """`(total latency, call count)` per seat, for the mean latency."""
    if not player_ids:
        return {}
    rows = await session.execute(
        sa.select(
            Turn.player_id,
            sa.func.coalesce(sa.func.sum(LlmCall.latency_ms), 0),
            sa.func.count(),
        )
        .select_from(LlmCall)
        .join(Turn, Turn.id == LlmCall.turn_id)
        .where(Turn.player_id.in_(player_ids))
        .group_by(Turn.player_id)
    )
    return {player_id: (int(total or 0), int(calls or 0)) for player_id, total, calls in rows}


def is_forfeit(termination: Termination | None) -> bool:
    from chessmark.game import FORFEIT_TERMINATIONS

    return termination in FORFEIT_TERMINATIONS
