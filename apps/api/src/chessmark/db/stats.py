"""What we know about one model, across every game it has played (BENCH-02, Phase 20).

Deliberately **not** the leaderboard's aggregate. `bench.service.compute_aggregates` answers "how
did this contestant do in the games that may be rated" — ranked games only, keyed by
`(model, quantization)`, because that is what a rating is allowed to see (BENCH-03). This answers
"what has this model actually done", over exhibition games, human games and ranked ones alike.

Two sources, on purpose:

* **Money and tokens come from `llm_calls`**, the row written per provider call. It is the same
  place invariant 4's costing lands, so a page cannot print a cost the call log disagrees with.
* **Results and illegal moves come from `players`**, because they are properties of a seat in a
  game rather than of any single call.

A model can hold both seats of one game. Seats are therefore counted separately from games, and
`wins + draws + losses` counts *seats* — a model that beat itself won one and lost one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, LlmCall, ModelRegistry, Player
from chessmark.game import GameResult


@dataclass(frozen=True, slots=True)
class ModelStats:
    """Everything a model page prints, and nothing it derives twice."""

    games: int
    """Distinct games this model appeared in, either seat."""

    seats: int
    """Seats held. Higher than `games` only when a model played itself."""

    wins: int
    draws: int
    losses: int
    forfeits: int

    illegal_attempts: int
    moves_played: int

    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_cost_usd: Decimal
    mean_latency_ms: float | None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def illegal_per_move(self) -> float:
        """The benchmark's headline number. Zero moves is zero, not a division by zero."""
        return self.illegal_attempts / self.moves_played if self.moves_played else 0.0

    @property
    def cost_per_game(self) -> Decimal:
        return self.total_cost_usd / self.games if self.games else Decimal(0)

    @property
    def cache_rate(self) -> float | None:
        """Cached share of the **prompt**, which is the only part that can be cached.

        `None` rather than zero when nothing has been sent: a model that has never played has no
        cache rate, and printing 0% would read as a measured failure.
        """
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else None


EMPTY = ModelStats(
    games=0,
    seats=0,
    wins=0,
    draws=0,
    losses=0,
    forfeits=0,
    illegal_attempts=0,
    moves_played=0,
    llm_calls=0,
    prompt_tokens=0,
    completion_tokens=0,
    cached_tokens=0,
    total_cost_usd=Decimal(0),
    mean_latency_ms=None,
)


async def model_stats(session: AsyncSession, model: ModelRegistry) -> ModelStats:
    """Aggregate one model over every game it has played.

    Only *finished* games count toward results — a running game has no result to attribute, and
    counting it as anything would move a number that is still being decided. Cost and tokens count
    from every call, including a game in progress, because that money is already spent.
    """
    seats = (
        await session.execute(
            sa.select(
                sa.func.count(Player.id).label("seats"),
                sa.func.count(sa.distinct(Player.game_id)).label("games"),
                sa.func.coalesce(sa.func.sum(Player.illegal_attempts), 0).label("illegal"),
                sa.func.coalesce(sa.func.sum(sa.cast(Player.forfeited, sa.Integer)), 0).label(
                    "forfeits"
                ),
            ).where(Player.model_id == model.id)
        )
    ).one()

    if not seats.seats:
        return EMPTY

    # Results, per seat, over finished games only.
    outcome = (
        await session.execute(
            sa.select(
                Game.result,
                Player.colour,
                sa.func.count().label("n"),
                sa.func.coalesce(sa.func.sum(Game.ply_count), 0).label("plies"),
            )
            .select_from(Player)
            .join(Game, Game.id == Player.game_id)
            .where(Player.model_id == model.id, Game.status == GameStatus.FINISHED)
            .group_by(Game.result, Player.colour)
        )
    ).all()

    wins = draws = losses = 0
    for row in outcome:
        if row.result == GameResult.DRAW:
            draws += row.n
        elif (row.result == GameResult.WHITE_WINS) == (row.colour == "white"):
            wins += row.n
        elif row.result in (GameResult.WHITE_WINS, GameResult.BLACK_WINS):
            losses += row.n

    # A seat's own moves: half the game's plies, and the halves are unequal when the count is odd.
    moves = (
        await session.execute(
            sa.select(
                sa.func.coalesce(
                    sa.func.sum(
                        sa.case(
                            (Player.colour == "white", (Game.ply_count + 1) / 2),
                            else_=Game.ply_count / 2,
                        )
                    ),
                    0,
                )
            )
            .select_from(Player)
            .join(Game, Game.id == Player.game_id)
            .where(Player.model_id == model.id)
        )
    ).scalar_one()

    # Money and tokens from the call log, so a page cannot disagree with what was billed.
    calls = (
        await session.execute(
            sa.select(
                sa.func.count(LlmCall.id).label("calls"),
                sa.func.coalesce(sa.func.sum(LlmCall.prompt_tokens), 0).label("prompt"),
                sa.func.coalesce(sa.func.sum(LlmCall.completion_tokens), 0).label("completion"),
                sa.func.coalesce(sa.func.sum(LlmCall.cached_tokens), 0).label("cached"),
                sa.func.coalesce(sa.func.sum(LlmCall.cost_usd), 0).label("cost"),
                sa.func.avg(LlmCall.latency_ms).label("latency"),
            ).where(LlmCall.model_slug == model.openrouter_id)
        )
    ).one()

    return ModelStats(
        games=int(seats.games),
        seats=int(seats.seats),
        wins=wins,
        draws=draws,
        losses=losses,
        forfeits=int(seats.forfeits),
        illegal_attempts=int(seats.illegal),
        moves_played=int(moves or 0),
        llm_calls=int(calls.calls),
        prompt_tokens=int(calls.prompt),
        completion_tokens=int(calls.completion),
        cached_tokens=int(calls.cached),
        total_cost_usd=Decimal(calls.cost),
        mean_latency_ms=float(calls.latency) if calls.latency is not None else None,
    )
