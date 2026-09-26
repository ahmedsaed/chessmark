"""Tournaments, where they meet the database.

The pure module in `chessmark.tournament` decides pairings and standings from results alone. This
is the other half: turning a `FieldFilter` into an actual field, writing the schedule down before
it is played, and reading back what happened.

**The schedule is persisted before any game is created.** That is what makes an event resumable
without replaying anything: restarting asks the table which pairings have no finished game yet,
rather than trusting the memory of a process that died.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import DECISION_VERSION
from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.agents.registry import endpoint_is_playable, model_is_playable
from chessmark.agents.tools import TOOL_SCHEMA_VERSION
from chessmark.bench.ratable import HARNESS_TERMINATIONS, decision_era, era
from chessmark.db.enums import GameStatus, ModelRuntime, TournamentStatus
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


def current_era(runtime: str = "llm") -> str:
    """The era the deployed code is playing, which is the one a new pairing joins.

    Composed here rather than in `bench` because this is where the deployed constants are known;
    `era` itself is pure and takes them as arguments, so it can be tested without importing half
    the application.

    **Per runtime** (ADR-0049). A decision event's task is the decision harness, so its era is that
    version's major and moves only when `DECISION_VERSION` does — a new chat prompt says nothing
    about what a decision model is asked, and must not open a new era in a pool of them.
    """
    if runtime == "decision":
        return decision_era(DECISION_VERSION)
    return era(PROMPT_VERSION, TOOL_SCHEMA_VERSION)


def era_of(tournament: Tournament) -> str:
    """The era a tournament is playing now: the current one for the kind of model it seats."""
    return current_era(filter_from_json(tournament.field_filter or {}).runtime)


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
        model_is_playable(min_context=0),
        # One definition of "an endpoint worth seating", shared with `select_endpoint` and the
        # catalogue. A field that admitted an entrant the picker then refused is how a pool spent
        # its pairings on a model whose only endpoint could not hold a game (AGENT-14).
        ModelRegistry.id.in_(sa.select(ModelEndpoint.model_id).where(*endpoint_is_playable())),
    )

    # A field is one runtime (ADR-0049), so a chat event never seats a decision model by default
    # and a decision event seats nothing else.
    query = query.where(ModelRegistry.runtime == ModelRuntime(field.runtime))
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
        games_per_pair=config.games_per_pair,
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
        # **Stored, because a pool re-resolves its field from this every tick.** Left out, a
        # decision pool would read back as a chat field and seat the wrong kind of model.
        "runtime": field.runtime,
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
        # Every event created before the key existed was a chat event.
        runtime=stored.get("runtime") or "llm",
    )


async def in_field(session: AsyncSession, tournament: Tournament, field: FieldFilter) -> set[str]:
    """Which seated entrants the field would still admit today.

    A pool re-resolves its field every tick to seat newcomers, and deliberately does **not**
    withdraw a model that has left — its games are real results and its rating is real, and
    dropping it because an endpoint went quiet would rewrite history (`admit_new_entrants`).

    But "keep its record" and "keep giving it games" are different instructions, and the pool was
    reading the second. `pool-free` seats 19 while the free tier serves 16: `minimax-m2.7`,
    `minimax-m3` and `glm-5.2` are gone from OpenRouter's free catalogue and were still being
    handed pairings — which the balance policy *prioritises*, because they have the fewest games
    (ADR-0041). Three delisted models were taking the pool's scarcest resource ahead of models that
    can actually play.
    """
    return {entrant.key for entrant in await resolve_field(session, field)}


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


async def pair_games(
    session: AsyncSession, tournament_id: uuid.UUID, *, era: str
) -> dict[frozenset[str], int]:
    """How many games each pair has had in this era, towards a pool's per-pair target (ADR-0050).

    **Every pairing that was not abandoned** — settled, running, paused or waiting to start. The
    ones not yet settled count because they will be: leaving them out would let the next tick
    schedule past the target while the first games are still being played. The abandoned ones do
    not, because an abandoned pairing produced no result (invariant 11) and a target of two games
    means two games that were actually decided, not two attempts the harness failed to finish.
    A bye has no pair.
    """
    rows = await session.execute(
        sa.select(TournamentGame.white_key, TournamentGame.black_key).where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.era == era,
            TournamentGame.black_key.is_not(None),
            TournamentGame.abandoned_reason.is_(None),
        )
    )
    counts: dict[frozenset[str], int] = {}
    for white, black in rows:
        pair = frozenset((white, black))
        counts[pair] = counts.get(pair, 0) + 1
    return counts


async def is_saturated(
    session: AsyncSession, tournament: Tournament, keys: set[str] | list[str]
) -> bool:
    """Whether every pair among `keys` has reached the pool's per-pair target this era (ADR-0050).

    What the page means by "idle, waiting for a newcomer". `False` for an event with no target —
    an open-ended pool is never saturated, however long it has run.
    """
    target = tournament.games_per_pair
    if target is None:
        return False
    counts = await pair_games(session, tournament.id, era=era_of(tournament))
    field = sorted(keys)
    return all(
        counts.get(frozenset((a, b)), 0) >= target
        for index, a in enumerate(field)
        for b in field[index + 1 :]
    )


async def record_round(
    session: AsyncSession,
    tournament_id: uuid.UUID,
    pairings: list[Pairing],
    *,
    era: str | None = None,
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
                # The caller's era, which knows what the event seats; the chat era otherwise,
                # which is what every event was before there was a second kind.
                era=era or current_era(),
            )
            # A bye is a scheduled point rather than a game, so it is settled on the spot.
            if pairing.is_bye:
                row.white_score = 1.0
                row.ended_at = sa.func.now()
            session.add(row)
        written.append(row)

    await session.flush()
    return written


async def results_so_far(
    session: AsyncSession, tournament_id: uuid.UUID, *, era: str | None = None
) -> list[Result]:
    """Every settled pairing of one era, in the shape the pure module pairs and ranks from.

    Abandoned pairings are omitted rather than scored: a game the harness could not run is not a
    finding about either player, and awarding it would put a loss on a record for our own failure.

    `era` defaults to every era, which is what a caller wanting the whole history asks for; the
    matchmaker and the live table pass the current one (ADR-0043).
    """
    rows = await session.scalars(
        sa.select(TournamentGame)
        .where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.white_score.is_not(None),
            TournamentGame.abandoned_reason.is_(None),
            *([TournamentGame.era == era] if era is not None else []),
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


async def attempted(
    session: AsyncSession, tournament_id: uuid.UUID, *, era: str | None = None
) -> list[Pairing]:
    """Every pairing written down that has produced no result — the other half of
    `results_so_far`.

    The matchmaker needs both halves to know what a rematch is. `results_so_far` deliberately
    withholds abandoned pairings because they must not be *scored* (invariant 11) — but "do not
    score it" and "pretend it never happened" are different instructions, and the pool was reading
    the second. A pairing that cannot complete therefore stayed permanently unmet, which is also
    permanently the most attractive thing to schedule: `gemma-4-26b` v `gemma-4-31b` was written
    down seven times over five days and never reached ply 1.

    Pairings still waiting or in flight are included for the same reason: a fixture already on the
    schedule is not one to write down a second copy of.
    """
    rows = await session.scalars(
        sa.select(TournamentGame)
        .where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.white_score.is_(None),
            *([TournamentGame.era == era] if era is not None else []),
        )
        .order_by(TournamentGame.round_number, TournamentGame.id)
    )
    return [
        Pairing(white=row.white_key, black=row.black_key, round_number=row.round_number)
        for row in rows
    ]


async def unplayed(
    session: AsyncSession,
    tournament_id: uuid.UUID,
    *,
    round_number: int | None = None,
    era: str | None = None,
) -> list[TournamentGame]:
    """Pairings with no result and no game in flight — what a restart should pick up."""
    query = sa.select(TournamentGame).where(
        TournamentGame.tournament_id == tournament_id,
        TournamentGame.white_score.is_(None),
        TournamentGame.abandoned_reason.is_(None),
        TournamentGame.game_id.is_(None),
        *([TournamentGame.era == era] if era is not None else []),
    )
    if round_number is not None:
        query = query.where(TournamentGame.round_number == round_number)
    rows = await session.scalars(query.order_by(TournamentGame.round_number, TournamentGame.id))
    return list(rows)


async def close_stale_pairings(session: AsyncSession, tournament_id: uuid.UUID, *, era: str) -> int:
    """Retire pairings written for a task the pool is no longer playing (ADR-0043).

    An era change leaves whatever was scheduled and unstarted behind it. Those fixtures will never
    run — the matchmaker is pairing for the new era now — and an `unplayed` row nothing will ever
    start is a fixture on the table that is a lie. Marked with a reason rather than deleted, so the
    old era's crosstable still shows what it had planned when it ended.

    Only rows with no game: anything already in flight belongs to its own era and finishes there.
    """
    stale = list(
        await session.scalars(
            sa.select(TournamentGame).where(
                TournamentGame.tournament_id == tournament_id,
                TournamentGame.era.is_not(None),
                TournamentGame.era != era,
                TournamentGame.white_score.is_(None),
                TournamentGame.abandoned_reason.is_(None),
                TournamentGame.game_id.is_(None),
            )
        )
    )
    for row in stale:
        row.abandoned_reason = f"the pool moved on to {era}"
        row.ended_at = dt.datetime.now(dt.UTC)
    return len(stale)


async def eras_of(session: AsyncSession, tournament_id: uuid.UUID) -> list[str]:
    """Every era this event has played, newest first.

    Read from the pairings rather than stored on the tournament: the eras an event has been through
    are a fact about what it played, and a second copy could disagree with it.
    """
    rows = await session.scalars(
        sa.select(TournamentGame.era)
        .where(TournamentGame.tournament_id == tournament_id, TournamentGame.era.is_not(None))
        .group_by(TournamentGame.era)
        .order_by(sa.func.max(TournamentGame.round_number).desc())
    )
    return [row for row in rows if row]


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


async def due_to_resume(
    session: AsyncSession, tournament_id: uuid.UUID, *, now: dt.datetime | None = None
) -> list[TournamentGame]:
    """Pairings paused past their `resume_after` — waiting on a slot rather than on a provider.

    A paused game holds no concurrency slot (ADR-0017), which is right while it is *waiting on the
    provider* and wrong the moment the wait is over: from then on it is queued behind whatever the
    runner starts next. Nothing counted it, so `_start_games` saw a free slot and filled it with a
    brand-new pairing, and the game that was ready to move went round again.

    That is not a small effect. Five abandoned games in `pool-free` were told by their providers to
    wait 9 minutes to 12 hours in total, and actually waited 20 to 27 — **48% to 99% of the pause
    was our own queue**. One was asked for sixty seconds and sat for 16.4 hours, all of it counting
    against the patience window that then abandoned it at ply 71.

    So a due game reserves its slot here, and the reconciler fills it on its next sweep.
    `resume_after IS NULL` counts as due for the same reason `find_resumable` includes it: a pause
    with no clock should not wait forever.
    """
    rows = await session.scalars(
        sa.select(TournamentGame)
        .join(Game, Game.id == TournamentGame.game_id)
        .where(
            TournamentGame.tournament_id == tournament_id,
            TournamentGame.white_score.is_(None),
            TournamentGame.abandoned_reason.is_(None),
            Game.status == GameStatus.PAUSED,
            sa.or_(
                Game.resume_after.is_(None),
                Game.resume_after <= (now or dt.datetime.now(dt.UTC)),
            ),
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
    """Make a pairing agree with its game. Returns whether anything changed.

    A game the harness stopped — its budget, its ply cap, a provider it could not reach — is
    **not** a result about the players, so it is marked abandoned rather than scored. That
    distinction is the same one the rating rules make (`bench/ratable.py`), and for the same
    reason: a forfeit is a finding, a harness failure is ours.

    **It reconciles rather than records, and that difference is load-bearing.** The first version
    only ever wrote to a pairing that had nothing yet, on the reasoning that a result is written
    once. A game can be *resumed*, though, and then the pairing holds a verdict its own game has
    outgrown — and because the caller skipped anything already scored, the stale verdict was
    permanent and blocked the real one. Seen on one page: a pairing scored 0-1 from an overturned
    forfeit whose game then drew by threefold repetition at ply 100, so the standings recorded a
    loss where there was a draw and no later tick would ever correct it.

    The game record is the authority (invariant 1), so this is the direction the disagreement is
    always resolved. Returning False when the pairing already agrees keeps it idempotent, which is
    what lets the caller offer every pairing on every tick.

    **The termination decides that, not the status** — and reading the status alone is what let the
    paragraph above be false for three of the seven harness endings. Only `ABANDONED` reaches here
    as `ABORTED`; a `PLY_CAP`, a `BUDGET_EXCEEDED` or an `ADJUDICATION` is a `FINISHED` game
    carrying a real `GameResult`, so it fell straight through to `_SCORES` and was scored like any
    other draw. `pool-free` round 175 is the one that showed it: `ling-3.0-flash-sante` reached
    `8/6P1/1k5P/5K2/5p2/8/8/8 w` — a pawn on g7, `g8=Q` on the move, the black king on b6 — and the
    300-ply cap drew it. `ratable.py` excluded it from the rating, correctly and invisibly, while
    this function handed both models half a point and the pool's table showed `0.5` beside
    `unrated`. Half of one seat's verdict came from our own ceiling.

    So this asks `HARNESS_TERMINATIONS` rather than keeping a fourth list of its own. That set was
    already the answer and nothing linked it: `test_classification.py` exists because three sets
    classifying terminations had drifted apart once, and this was the fourth, unlinked and one
    module away.
    """
    harness_stopped = game.termination in HARNESS_TERMINATIONS
    if game.status is GameStatus.ABORTED or harness_stopped:
        if row.abandoned_reason and row.white_score is None:
            return False
        row.abandoned_reason = game.termination_detail or "the game was abandoned"
        row.white_score = None
        row.ended_at = sa.func.now()
        await session.flush()
        return False

    score = _SCORES.get(game.result)
    if game.status is not GameStatus.FINISHED or score is None:
        # In flight, whatever the pairing may still say. `white_score` means *decided* — the
        # column's own comment is "null while the game is unplayed or in flight" — so a leftover
        # score here is what drew four running games as **played** and reported `live: 0` while
        # four boards moved.
        if row.white_score is None:
            return False
        row.white_score = None
        row.ended_at = None
        await session.flush()
        return False

    if row.white_score == score and row.abandoned_reason is None:
        return False

    row.white_score = score
    # **A real result overrides a recorded abandonment**, because the two cannot both be true. A
    # game abandoned on a provider 404 was resumed, played on to checkmate at ply 120 — and its
    # pairing stayed "abandoned, no score" for ever.
    row.abandoned_reason = None
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
