"""Tournaments, where they meet the database.

The pure module in `chessmark.tournament` decides pairings and standings from results alone. This
is the other half: turning a `FieldFilter` into an actual field, writing the schedule down before
it is played, and reading back what happened.

**The schedule is persisted before any game is created.** That is what makes an event resumable
without replaying anything: restarting asks the table which pairings have no finished game yet,
rather than trusting the memory of a process that died.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import endpoint_is_playable
from chessmark.db.enums import GameStatus, TournamentStatus
from chessmark.db.models import (
    Game,
    ModelEndpoint,
    ModelRegistry,
    Player,
    Tournament,
    TournamentEntrant,
    TournamentGame,
)
from chessmark.game import GameResult
from chessmark.tournament import (
    Entrant,
    FieldFilter,
    Pairing,
    Result,
    TournamentConfig,
)


def contestant_key(model_slug: str, quantization: str | None) -> str:
    """The identity the pure module pairs on.

    A contestant is `(model, quantization)` (ADR-0015): the same weights at fp8 and fp4 are
    different entrants, because the precision changes the result as much as the model does. A
    tournament that does not pin a precision leaves it off the key and lets the router choose,
    which is honest so long as the game records what it actually played at.
    """
    return f"{model_slug}@{quantization}" if quantization else model_slug


async def resolve_field(
    session: AsyncSession, field: FieldFilter, *, seeded_by_cost: bool = True
) -> list[Entrant]:
    """Every model matching the filter, as entrants.

    Only models that can actually finish a game are eligible — enabled, tool-capable, and holding
    at least one active tool-capable endpoint. A model with no live endpoint has no contestants and
    cannot be picked (ADR-0015); entering it would schedule games that forfeit at ply 0 and put a
    loss on the record of whoever it was paired against.

    Every criterion is optional and they compose with AND, so one query serves every bracket:
    `free_only=True` is the free-model event, `open_weights=True` is one side of open against
    closed, `providers=(...)` is a vendor or country bracket.
    """
    query = sa.select(ModelRegistry).where(
        ModelRegistry.enabled.is_(True),
        ModelRegistry.supports_tools.is_(True),
        # One definition of "an endpoint worth seating", shared with `select_endpoint` and the
        # catalogue. A field that admitted an entrant the picker then refused is how a pool spent
        # its pairings on a model whose only endpoint could not hold a game (AGENT-14).
        ModelRegistry.id.in_(sa.select(ModelEndpoint.model_id).where(*endpoint_is_playable())),
    )

    if field.slugs:
        query = query.where(ModelRegistry.openrouter_id.in_(field.slugs))
    if field.providers:
        query = query.where(ModelRegistry.provider.in_(field.providers))
    if field.free_only is not None:
        query = query.where(ModelRegistry.is_free.is_(field.free_only))
    if field.open_weights is True:
        query = query.where(ModelRegistry.hugging_face_id.is_not(None))
    elif field.open_weights is False:
        query = query.where(ModelRegistry.hugging_face_id.is_(None))
    if field.requires_reasoning is not None:
        query = query.where(ModelRegistry.supports_reasoning.is_(field.requires_reasoning))
    if field.min_context_tokens is not None:
        query = query.where(ModelRegistry.context_length >= field.min_context_tokens)

    # The effective price is the override when an administrator has set one (ADR-0016), so the
    # filter must read the same number the picker charges rather than the derived tier.
    cost = sa.func.coalesce(ModelRegistry.credit_cost_override, ModelRegistry.credit_cost)
    if field.min_credit_cost is not None:
        query = query.where(cost >= field.min_credit_cost)
    if field.max_credit_cost is not None:
        query = query.where(cost <= field.max_credit_cost)

    # Seeding by price is a stand-in for strength before anyone has played: expensive models are
    # generally stronger, and a Swiss first round pairs on seed. It is a guess, and it stops
    # mattering the moment there are results.
    order = (
        (cost.desc(), ModelRegistry.openrouter_id)
        if seeded_by_cost
        else (ModelRegistry.openrouter_id,)
    )
    rows = list(await session.scalars(query.order_by(*order)))
    if field.limit is not None:
        rows = rows[: field.limit]

    return [
        Entrant(key=contestant_key(row.openrouter_id, None), seed=index, label=row.display_name)
        for index, row in enumerate(rows, start=1)
    ]


async def create_tournament(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    config: TournamentConfig,
    entrants: list[Entrant],
) -> Tournament:
    """Create the event and seat its field. The schedule is written separately, per round."""
    tournament = Tournament(
        name=name,
        slug=slug,
        status=TournamentStatus.PENDING,
        format=str(config.format),
        double=config.double,
        rounds=config.rounds,
        field_filter=_filter_as_json(config.field),
        max_concurrent=config.max_concurrent,
        max_usd=config.max_usd,
        max_plies_per_game=config.max_plies_per_game,
        max_usd_per_game=config.max_usd_per_game,
        is_ranked=config.is_ranked,
    )
    session.add(tournament)
    await session.flush()

    slugs = [key.split("@", 1)[0] for key in (e.key for e in entrants)]
    known = {
        row.openrouter_id: row
        for row in await session.scalars(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id.in_(slugs))
        )
    }

    for entrant in entrants:
        model_slug, _, quantization = entrant.key.partition("@")
        row = known.get(model_slug)
        session.add(
            TournamentEntrant(
                tournament_id=tournament.id,
                model_id=row.id if row else None,
                key=entrant.key,
                model_slug=model_slug,
                quantization=quantization or None,
                display_name=entrant.label or model_slug,
                seed=entrant.seed,
            )
        )

    await session.flush()
    return tournament


def _filter_as_json(field: FieldFilter) -> dict[str, Any]:
    """The filter, stored so a standings page can say what it selected."""
    return {
        "slugs": list(field.slugs),
        "providers": list(field.providers),
        "free_only": field.free_only,
        "open_weights": field.open_weights,
        "min_credit_cost": field.min_credit_cost,
        "max_credit_cost": field.max_credit_cost,
        "min_context_tokens": field.min_context_tokens,
        "requires_reasoning": field.requires_reasoning,
        "limit": field.limit,
        "describes": field.describe(),
    }


async def admit_new_entrants(
    session: AsyncSession, tournament: Tournament, field: FieldFilter
) -> list[str]:
    """Re-resolve a pool's field and seat anybody new. Returns the keys admitted.

    Only pools do this. A closed event's field is frozen because its fixture list is computed from
    it — a latecomer would invalidate the schedule, and a table whose rows played different
    opponents means different things per row. A pool has neither problem: Glicko-2 is built for an
    open population, so a model listed today can start at 1500 +/- 350 and settle by playing.

    A model that has *left* the catalogue is not withdrawn here. Its games are real results and its
    rating is real; dropping it automatically would rewrite history because an endpoint went quiet
    for an afternoon. `withdraw` stays a deliberate act.
    """
    seated = set(
        await session.scalars(
            sa.select(TournamentEntrant.key).where(TournamentEntrant.tournament_id == tournament.id)
        )
    )
    eligible = await resolve_field(session, field)
    newcomers = [entrant for entrant in eligible if entrant.key not in seated]
    if not newcomers:
        return []

    highest = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.max(TournamentEntrant.seed), 0)).where(
            TournamentEntrant.tournament_id == tournament.id
        )
    )
    slugs = [key.split("@", 1)[0] for key in (e.key for e in newcomers)]
    known = {
        row.openrouter_id: row
        for row in await session.scalars(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id.in_(slugs))
        )
    }

    for offset, entrant in enumerate(newcomers, start=1):
        model_slug, _, quantization = entrant.key.partition("@")
        row = known.get(model_slug)
        session.add(
            TournamentEntrant(
                tournament_id=tournament.id,
                model_id=row.id if row else None,
                key=entrant.key,
                model_slug=model_slug,
                quantization=quantization or None,
                display_name=entrant.label or model_slug,
                seed=int(highest or 0) + offset,
            )
        )

    await session.flush()
    return [entrant.key for entrant in newcomers]


def filter_from_json(stored: dict[str, Any]) -> FieldFilter:
    """The stored filter, back as the thing that resolved it.

    A pool re-runs its own selection every tick, so the description written at creation has to be
    executable again rather than merely readable.
    """
    return FieldFilter(
        slugs=tuple(stored.get("slugs") or ()),
        providers=tuple(stored.get("providers") or ()),
        free_only=stored.get("free_only"),
        open_weights=stored.get("open_weights"),
        min_credit_cost=stored.get("min_credit_cost"),
        max_credit_cost=stored.get("max_credit_cost"),
        min_context_tokens=stored.get("min_context_tokens"),
        requires_reasoning=stored.get("requires_reasoning"),
        limit=stored.get("limit"),
    )


async def entrants_of(session: AsyncSession, tournament_id: uuid.UUID) -> list[Entrant]:
    rows = await session.scalars(
        sa.select(TournamentEntrant)
        .where(
            TournamentEntrant.tournament_id == tournament_id,
            TournamentEntrant.withdrawn.is_(False),
        )
        .order_by(TournamentEntrant.seed, TournamentEntrant.key)
    )
    return [Entrant(key=r.key, seed=r.seed, label=r.display_name) for r in rows]


async def record_round(
    session: AsyncSession, tournament_id: uuid.UUID, pairings: list[Pairing]
) -> list[TournamentGame]:
    """Write a round's pairings down before any of them is played.

    Idempotent on `(tournament, round, white, black)`: re-recording a round a crash left half
    written adds nothing, which is what lets the runner replay its own start-up safely.
    """
    existing = {
        (row.round_number, row.white_key, row.black_key): row
        for row in await session.scalars(
            sa.select(TournamentGame).where(
                TournamentGame.tournament_id == tournament_id,
                TournamentGame.round_number.in_({p.round_number for p in pairings}),
            )
        )
    }

    written: list[TournamentGame] = []
    for pairing in pairings:
        key = (pairing.round_number, pairing.white, pairing.black)
        row = existing.get(key)
        if row is None:
            row = TournamentGame(
                tournament_id=tournament_id,
                round_number=pairing.round_number,
                white_key=pairing.white,
                black_key=pairing.black,
            )
            # A bye is a scheduled point rather than a game, so it is settled on the spot.
            if pairing.is_bye:
                row.white_score = 1.0
                row.ended_at = sa.func.now()
            session.add(row)
        written.append(row)

    await session.flush()
    return written


async def results_so_far(session: AsyncSession, tournament_id: uuid.UUID) -> list[Result]:
    """Every settled pairing, in the shape the pure module pairs and ranks from.

    Abandoned pairings are omitted rather than scored: a game the harness could not run is not a
    finding about either player, and awarding it would put a loss on a record for our own failure.
    """
    rows = await session.scalars(
        sa.select(TournamentGame)
        .where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.white_score.is_not(None),
            TournamentGame.abandoned_reason.is_(None),
        )
        .order_by(TournamentGame.round_number, TournamentGame.id)
    )
    return [
        Result(
            white=row.white_key,
            black=row.black_key,
            white_score=float(row.white_score or 0.0),
            round_number=row.round_number,
        )
        for row in rows
    ]


async def unplayed(
    session: AsyncSession, tournament_id: uuid.UUID, *, round_number: int | None = None
) -> list[TournamentGame]:
    """Pairings with no result and no game in flight — what a restart should pick up."""
    query = sa.select(TournamentGame).where(
        TournamentGame.tournament_id == tournament_id,
        TournamentGame.white_score.is_(None),
        TournamentGame.abandoned_reason.is_(None),
        TournamentGame.game_id.is_(None),
    )
    if round_number is not None:
        query = query.where(TournamentGame.round_number == round_number)
    rows = await session.scalars(query.order_by(TournamentGame.round_number, TournamentGame.id))
    return list(rows)


async def in_flight(session: AsyncSession, tournament_id: uuid.UUID) -> list[TournamentGame]:
    """Pairings whose game exists and has not finished — what bounds concurrency."""
    rows = await session.scalars(
        sa.select(TournamentGame)
        .join(Game, Game.id == TournamentGame.game_id)
        .where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.white_score.is_(None),
            TournamentGame.abandoned_reason.is_(None),
            Game.status.in_({GameStatus.PENDING, GameStatus.RUNNING}),
        )
    )
    return list(rows)


#: How a finished game maps onto White's score.
_SCORES = {
    GameResult.WHITE_WINS: 1.0,
    GameResult.BLACK_WINS: 0.0,
    GameResult.DRAW: 0.5,
}


async def settle(session: AsyncSession, row: TournamentGame, game: Game) -> bool:
    """Record a finished game's result against its pairing. Returns whether it settled.

    A game the harness stopped — its budget, its ply cap, a provider it could not reach — is
    **not** a result about the players, so it is marked abandoned rather than scored. That
    distinction is the same one the rating rules make (`bench/ratable.py`), and for the same
    reason: a forfeit is a finding, a harness failure is ours.
    """
    if game.status is GameStatus.ABORTED:
        row.abandoned_reason = game.termination_detail or "the game was abandoned"
        row.ended_at = sa.func.now()
        await session.flush()
        return False

    score = _SCORES.get(game.result)
    if game.status is not GameStatus.FINISHED or score is None:
        return False

    row.white_score = score
    row.ended_at = sa.func.now()
    await session.flush()
    return True


async def spent(session: AsyncSession, tournament_id: uuid.UUID) -> Decimal:
    """What this event has cost, summed from the games it actually ran.

    Read from `games.total_cost_usd` rather than accumulated in the tournament row, so the figure
    cannot drift from the call log the way a running total can (invariant 4).
    """
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Game.total_cost_usd), 0))
        .select_from(TournamentGame)
        .join(Game, Game.id == TournamentGame.game_id)
        .where(TournamentGame.tournament_id == tournament_id)
    )
    return Decimal(str(total or 0))


async def seats_for(session: AsyncSession, game_id: uuid.UUID) -> dict[str, str]:
    """Colour to model slug, for a game already created."""
    rows = await session.scalars(sa.select(Player).where(Player.game_id == game_id))
    return {str(row.colour): row.display_name for row in rows}
