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
from chessmark.bench.ratable import GameFacts, Verdict, judge
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, LlmCall, ModelRegistry, Player, Rating, Turn
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


async def _facts_for(session: AsyncSession, game: Game, players: list[Player]) -> GameFacts:
    """Everything `judge` needs, read once per game."""
    used: dict[uuid.UUID, tuple[str, ...]] = {}
    rows = await session.execute(
        sa.select(Turn.player_id, LlmCall.provider)
        .join(LlmCall, LlmCall.turn_id == Turn.id)
        .where(Turn.game_id == game.id, LlmCall.provider.is_not(None))
        .distinct()
    )
    for player_id, provider in rows:
        used[player_id] = (*used.get(player_id, ()), str(provider))

    return GameFacts(
        is_ranked=game.is_ranked,
        termination=game.termination,
        prompt_version=game.prompt_version,
        pinned_providers=tuple(_pinned(p) for p in players),
        used_providers=tuple(used.get(p.id, ()) for p in players),
        model_slugs=tuple(str((p.sampling or {}).get("model") or "") for p in players),
        trash_talk_enabled=game.trash_talk_enabled,
    )


def _pinned(player: Player) -> str | None:
    only = (player.provider_routing or {}).get("only") or []
    return str(only[0]) if only else None


def _score(result: GameResult, colour: Colour) -> float:
    if result is GameResult.DRAW:
        return 0.5
    if result is GameResult.WHITE_WINS:
        return 1.0 if colour is Colour.WHITE else 0.0
    return 1.0 if colour is Colour.BLACK else 0.0


async def _contestant(
    session: AsyncSession, player: Player, quantizations: dict[uuid.UUID, str]
) -> Contestant | None:
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


async def _quantization_by_player(
    session: AsyncSession, game_id: uuid.UUID, players: list[Player]
) -> dict[uuid.UUID, str]:
    """The precision each seat played at.

    From the **pinned** endpoint first, because that is where the contestant's identity is decided —
    at match creation, before a single call is made (ADR-0015). Inferring it afterwards from
    `llm_calls` was the first version and it is strictly worse: a game that made no calls, or whose
    provider row has since been renamed, silently becomes `unknown` and lands in the wrong
    leaderboard row.

    Falls back to what actually served, for games played before pinning existed.
    """
    from chessmark.db.models import ModelEndpoint

    pinned: dict[uuid.UUID, str] = {}
    for player in players:
        provider = _pinned(player)
        if provider is None or player.model_id is None:
            continue
        quantization = await session.scalar(
            sa.select(ModelEndpoint.quantization).where(
                ModelEndpoint.model_id == player.model_id,
                ModelEndpoint.provider_name == provider,
            )
        )
        if quantization is not None:
            pinned[player.id] = quantization

    rows = await session.execute(
        sa.select(Turn.player_id, ModelEndpoint.quantization)
        .join(LlmCall, LlmCall.turn_id == Turn.id)
        .join(ModelRegistry, ModelRegistry.openrouter_id == LlmCall.model_slug)
        .join(
            ModelEndpoint,
            sa.and_(
                ModelEndpoint.model_id == ModelRegistry.id,
                ModelEndpoint.provider_name == LlmCall.provider,
            ),
        )
        .where(Turn.game_id == game_id)
        .distinct()
    )
    served = {player_id: (quantization or "unknown") for player_id, quantization in rows}

    return {
        player.id: pinned.get(player.id) or served.get(player.id, "unknown") for player in players
    }


async def compute_ratings(
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION, tau: float = 0.5
) -> RatingRun:
    """Rebuild every rating from every eligible game.

    Games are grouped into periods and each period is rated as a batch — Glicko-2 is defined that
    way, and rating game by game gives a different and less defensible answer.
    """
    system = Glicko2(tau=tau)
    run = RatingRun()

    games = list(
        await session.scalars(
            sa.select(Game)
            .where(Game.status.in_([GameStatus.FINISHED, GameStatus.ABORTED]))
            .order_by(Game.created_at)
        )
    )

    by_period: dict[int, list[tuple[Game, list[Player], dict[uuid.UUID, str]]]] = {}

    for game in games:
        players = list(await session.scalars(sa.select(Player).where(Player.game_id == game.id)))
        verdict: Verdict = judge(
            await _facts_for(session, game, players), prompt_version=prompt_version
        )
        if not verdict:
            run.excluded.append(Excluded(game_id=game.id, reason=verdict.reason))
            continue

        quantizations = await _quantization_by_player(session, game.id, players)
        period = period_of(game.ended_at or game.created_at)
        by_period.setdefault(period, []).append((game, players, quantizations))
        run.games_counted += 1

    run.periods = sorted(by_period)

    for period in run.periods:
        # Every contestant seen so far is rated for this period, including those who did not play:
        # an idle period must widen the deviation, or a stale rating keeps its confidence forever.
        outcomes: dict[Contestant, list[Outcome]] = {c: [] for c in run.ratings}

        for game, players, quantizations in by_period[period]:
            seats: list[tuple[Contestant, Player]] = []
            for player in players:
                contestant = await _contestant(session, player, quantizations)
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
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION
) -> dict[Contestant, Aggregate]:
    """Per-contestant metrics over the same games the ratings used.

    The *same* eligibility rules, deliberately: a leaderboard whose rating and whose illegal-move
    rate were computed over different sets of games would be quietly incoherent.
    """
    aggregates: dict[Contestant, Aggregate] = {}

    games = list(
        await session.scalars(
            sa.select(Game)
            .where(Game.status.in_([GameStatus.FINISHED, GameStatus.ABORTED]))
            .order_by(Game.created_at)
        )
    )

    for game in games:
        players = list(await session.scalars(sa.select(Player).where(Player.game_id == game.id)))
        if not judge(await _facts_for(session, game, players), prompt_version=prompt_version):
            continue

        quantizations = await _quantization_by_player(session, game.id, players)

        for player in players:
            contestant = await _contestant(session, player, quantizations)
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

            moves = await session.scalar(
                sa.select(sa.func.count())
                .select_from(Turn)
                .where(Turn.player_id == player.id, Turn.ply_number.is_not(None))
            )
            entry.moves_played += int(moves or 0)

            latency = await session.execute(
                sa.select(sa.func.coalesce(sa.func.sum(LlmCall.latency_ms), 0), sa.func.count())
                .select_from(LlmCall)
                .join(Turn, Turn.id == LlmCall.turn_id)
                .where(Turn.player_id == player.id)
            )
            total_latency, calls = latency.one()
            entry.latency_ms_total += int(total_latency or 0)
            entry.llm_calls += int(calls or 0)

    return aggregates


def is_forfeit(termination: Termination | None) -> bool:
    from chessmark.game import FORFEIT_TERMINATIONS

    return termination in FORFEIT_TERMINATIONS
