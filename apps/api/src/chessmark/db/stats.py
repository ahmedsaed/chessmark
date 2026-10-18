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

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import GameStatus, PlayerKind
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
            )
            # Answered calls only (ADR-0062): a failed attempt is a row too, with no tokens, no
            # cost and a latency that measures the provider failing rather than the model working.
            .where(LlmCall.model_slug == model.openrouter_id, LlmCall.error.is_(None))
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


async def last_played(
    session: AsyncSession, model_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dt.datetime]:
    """When each model last finished a game, in one grouped statement however many are asked for.

    Finished games only: a running game has not produced the page a search engine would index, and
    an aborted one never will. `ended_at` falls back to `created_at` because a row finished before
    the column was always written still finished, and dropping it would drop the model's page.
    """
    if not model_ids:
        return {}
    rows = await session.execute(
        sa.select(Player.model_id, sa.func.max(sa.func.coalesce(Game.ended_at, Game.created_at)))
        .join(Game, Game.id == Player.game_id)
        .where(Player.model_id.in_(model_ids), Game.status == GameStatus.FINISHED)
        .group_by(Player.model_id)
    )
    return {model_id: at for model_id, at in rows.tuples() if model_id is not None}


# ====================================================================== the model page's prose


#: How many openings per colour the page lists. Opponents are all listed: there are at most as many
#: as there are models, and the head-to-head table is the most useful thing on the page.
TOP_OPENINGS = 3


@dataclass(frozen=True, slots=True)
class Record:
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    def add(self, outcome: str, n: int) -> Record:
        return Record(
            wins=self.wins + (n if outcome == "won" else 0),
            draws=self.draws + (n if outcome == "drew" else 0),
            losses=self.losses + (n if outcome == "lost" else 0),
        )


@dataclass(frozen=True, slots=True)
class Opponent:
    #: Null for a person: people are counted together as one opponent, never named.
    openrouter_id: str | None
    display_name: str
    kind: str
    record: Record


@dataclass(frozen=True, slots=True)
class Ending:
    termination: str
    record: Record


@dataclass(frozen=True, slots=True)
class OpeningCount:
    eco: str
    name: str
    games: int


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """What a model page says in sentences, beside the numbers `ModelStats` gives it (ADR-0069).

    **Computed per request, not stored**, unlike a game's digest. A game never changes once it is
    over; a model's profile changes with every game it plays, so a stored copy would be a second
    derived table to keep current. The page's read is already cached by the web tier and expired
    when a game moves (ADR-0046), and this is five grouped statements however many games there
    are — `test_a_model_page_costs_a_fixed_number_of_queries` holds that.
    """

    by_colour: dict[str, Record]
    endings: list[Ending]
    opponents: list[Opponent]
    #: Per colour, from the games whose digest named an opening. A game is named once its digest
    #: exists, so before `make backfill-digests` this counts only games someone has opened.
    openings: dict[str, list[OpeningCount]]
    #: Illegal attempts per move across every model seat — what "often" means for this one.
    field_illegal_per_move: float | None


def _outcome(seat: Any) -> Any:
    """`won`, `drew` or `lost` for a seat, from the game's result. Null for a game with none."""
    return sa.case(
        (
            sa.or_(
                sa.and_(Game.result == GameResult.WHITE_WINS, seat.colour == "white"),
                sa.and_(Game.result == GameResult.BLACK_WINS, seat.colour == "black"),
            ),
            "won",
        ),
        (Game.result == GameResult.DRAW, "drew"),
        (Game.result.in_((GameResult.WHITE_WINS, GameResult.BLACK_WINS)), "lost"),
        else_=None,
    )


def _seat_moves(seat: Any) -> Any:
    """A seat's own moves: half the game's plies, the halves unequal when the count is odd."""
    return sa.case(
        (seat.colour == "white", (Game.ply_count + 1) / 2),
        else_=Game.ply_count / 2,
    )


async def model_profile(session: AsyncSession, model: ModelRegistry) -> ModelProfile:
    me = sa.orm.aliased(Player)
    them = sa.orm.aliased(Player)
    finished = [me.model_id == model.id, Game.status == GameStatus.FINISHED]

    # 1. Results by colour and by how the game ended, in one pass.
    outcome = _outcome(me).label("outcome")
    by_colour: dict[str, Record] = {"white": Record(), "black": Record()}
    endings: dict[str, Record] = {}
    for colour, termination, result, n in (
        await session.execute(
            sa.select(me.colour, Game.termination, outcome, sa.func.count())
            .select_from(me)
            .join(Game, Game.id == me.game_id)
            .where(*finished)
            .group_by(me.colour, Game.termination, outcome)
        )
    ).tuples():
        if result is None:
            continue
        by_colour[str(colour)] = by_colour[str(colour)].add(result, n)
        key = str(termination) if termination is not None else "unknown"
        endings[key] = endings.get(key, Record()).add(result, n)

    # 2. Who it played. A model that played itself meets itself as an opponent once per seat.
    opponents: dict[tuple[str, str | None], Opponent] = {}
    for kind, slug, name, seat_name, result, n in (
        await session.execute(
            sa.select(
                them.kind,
                ModelRegistry.openrouter_id,
                ModelRegistry.display_name,
                sa.func.min(them.display_name),
                outcome,
                sa.func.count(),
            )
            .select_from(me)
            .join(Game, Game.id == me.game_id)
            .join(them, sa.and_(them.game_id == me.game_id, them.id != me.id))
            .outerjoin(ModelRegistry, ModelRegistry.id == them.model_id)
            .where(*finished)
            .group_by(them.kind, ModelRegistry.openrouter_id, ModelRegistry.display_name, outcome)
        )
    ).tuples():
        if result is None:
            continue
        person = str(kind) == str(PlayerKind.HUMAN)
        who: tuple[str, str | None] = ("human", None) if person else ("model", slug)
        current = opponents.get(who) or Opponent(
            openrouter_id=None if person else slug,
            display_name="a person" if person else (name or seat_name),
            kind="human" if person else "model",
            record=Record(),
        )
        opponents[who] = Opponent(
            openrouter_id=current.openrouter_id,
            display_name=current.display_name,
            kind=current.kind,
            record=current.record.add(result, n),
        )

    # 3. The openings it reached, from each game's stored digest.
    eco = Game.digest["opening"]["eco"].as_string()
    name = Game.digest["opening"]["name"].as_string()
    openings: dict[str, list[OpeningCount]] = {"white": [], "black": []}
    for colour, code, title, n in (
        await session.execute(
            sa.select(me.colour, eco, name, sa.func.count())
            .select_from(me)
            .join(Game, Game.id == me.game_id)
            .where(*finished, eco.is_not(None))
            .group_by(me.colour, eco, name)
            .order_by(sa.func.count().desc(), eco)
        )
    ).tuples():
        bucket = openings[str(colour)]
        if len(bucket) < TOP_OPENINGS:
            bucket.append(OpeningCount(eco=code, name=title, games=n))

    # 4. The field: every model seat's illegal attempts per move, the same formula as this one's.
    illegal, moves = (
        await session.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(Player.illegal_attempts), 0),
                sa.func.coalesce(sa.func.sum(_seat_moves(Player)), 0),
            )
            .select_from(Player)
            .join(Game, Game.id == Player.game_id)
            .where(Player.kind == PlayerKind.MODEL)
        )
    ).one()

    return ModelProfile(
        by_colour=by_colour,
        endings=sorted(
            (Ending(termination=key, record=record) for key, record in endings.items()),
            key=lambda ending: (-ending.record.games, ending.termination),
        ),
        opponents=sorted(
            opponents.values(),
            key=lambda opponent: (-opponent.record.games, opponent.display_name),
        ),
        openings=openings,
        field_illegal_per_move=(float(illegal) / float(moves)) if moves else None,
    )
