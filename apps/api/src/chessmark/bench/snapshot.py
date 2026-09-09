"""The stored leaderboard, and how it is kept honest (ADR-0032).

`service.py` computes the ranking; this decides **when**. The answer is: when a game ends, not when
a page is read.

The leaderboard was rebuilt from raw rows on every request, and four pages await it — two of which
display no rating at all. `/methodology` renders three scalars and paid for a full Glicko-2 run to
get them. Games reach a terminal state around ten times a day; those pages are read on every visit,
so the computation was on the wrong side of the ledger by three orders of magnitude.

**The stored run is a cache, not a source of truth.** Delete every row and the next request rebuilds
it. Nothing here may be read to decide anything a game record could answer.

What makes that safe is the fingerprint. Recomputing forever was the old answer to "a stored number
could quietly stop matching its games"; recording what the run was computed from is a better one,
because it makes the disagreement *detectable* rather than impossible. A read whose fingerprint
does not match recomputes inline and stores the result — so a snapshot missed by a crash, or a game
ended by a path that forgot to trigger, costs one slow request and then self-heals.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.bench.service import (
    TERMINAL,
    Aggregate,
    Contestant,
    compute_aggregates,
    compute_ratings,
    scan,
)
from chessmark.db.models import Game, LeaderboardSnapshot, ModelEndpoint


async def fingerprint(session: AsyncSession, *, prompt_version: str | None) -> str:
    """What a run was computed from, in two cheap indexed aggregates.

    Covers the inputs that can change a *number*:

    * the terminal games — their count and the latest ending, which together move whenever a game
      finishes, is aborted, or is deleted;
    * `model_endpoints` — because the precision an endpoint serves is half a contestant's identity
      (ADR-0015), so an edit there can move a row from `model@fp8` to `model@fp4`;
    * the prompt version, since a game played under an older prompt measured a different task.

    Deliberately **not** covered: `model_registry.display_name`. It is a label, resolved at read,
    and baking it in would force a rebuild for a cosmetic rename.
    """
    games = (
        await session.execute(
            sa.select(sa.func.count(), sa.func.max(Game.ended_at)).where(Game.status.in_(TERMINAL))
        )
    ).one()
    endpoints = (
        await session.execute(
            sa.select(sa.func.count(), sa.func.max(ModelEndpoint.id)).select_from(ModelEndpoint)
        )
    ).one()

    return f"{prompt_version}|games={games[0]}@{games[1]}|endpoints={endpoints[0]}@{endpoints[1]}"


def _jsonable(value: Any) -> Any:
    """`Decimal` and `UUID` survive the round trip as strings, which is what the schema reads."""
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(inner) for inner in value]
    return value


async def build(
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION
) -> dict[str, Any]:
    """Run the whole computation and return it as the stored shape.

    One scan feeds the ratings, the aggregates and the drill-down, exactly as the request path did —
    the arithmetic is unchanged, only its timing.
    """
    scanned = await scan(session, prompt_version=prompt_version)
    run = await compute_ratings(session, prompt_version=prompt_version, scanned=scanned)
    aggregates = await compute_aggregates(session, prompt_version=prompt_version, scanned=scanned)

    # The games behind each row, so the drill-down does not have to scan either (BENCH-02).
    #
    # **Both seats, every game.** A game is indexed under each contestant that played it: it is one
    # game for white and the same game for black, and a row that cannot reach half its own games is
    # exactly the "take it on faith" this drill-down exists to refuse. Seats arrive ordered by
    # colour, so recording only the first gave black every game and white none — the counts come
    # from a different pass over the same scan and stayed right, which is what let a row say fifteen
    # while its page listed eight.
    counted: dict[str, list[str]] = {}
    for game, players, quantizations in scanned.counted:
        for player in players:
            slug = str((player.sampling or {}).get("model") or "")
            if not slug:
                continue
            key = f"{slug}@{quantizations.get(player.id, 'unknown')}"
            ids = counted.setdefault(key, [])
            # A model on both sides of a mirror match is one contestant holding two seats, and
            # listing the game twice would make the page disagree with the row the other way.
            # Appends for one game are consecutive within a label, so the last id is the whole
            # check.
            if not ids or ids[-1] != str(game.id):
                ids.append(str(game.id))

    payload: dict[str, Any] = {
        "rows": [
            _row(contestant, rating, aggregates.get(contestant))
            for contestant, rating in run.ratings.items()
        ],
        "games_counted": run.games_counted,
        "excluded": [{"game_id": e.game_id, "reason": e.reason} for e in run.excluded],
        "prompt_version": prompt_version,
        "periods": len(run.periods),
        "games_by_contestant": counted,
    }
    return dict(_jsonable(payload))


def _row(contestant: Contestant, rating: Any, aggregate: Aggregate | None) -> dict[str, Any]:
    """One contestant's numbers. No `display_name` — that is resolved at read."""
    return {
        "model_id": contestant.model_id,
        "model_slug": contestant.model_slug,
        "quantization": contestant.quantization,
        "rating": rating.rating,
        "rating_deviation": rating.rd,
        "volatility": rating.volatility,
        "provisional": rating.provisional,
        "games": aggregate.games if aggregate else 0,
        "wins": aggregate.wins if aggregate else 0,
        "draws": aggregate.draws if aggregate else 0,
        "losses": aggregate.losses if aggregate else 0,
        "illegal_attempts": aggregate.illegal_attempts if aggregate else 0,
        "moves_played": aggregate.moves_played if aggregate else 0,
        "illegal_per_move": aggregate.illegal_per_move if aggregate else 0.0,
        "forfeits": aggregate.forfeits if aggregate else 0,
        "mean_cost_usd": aggregate.mean_cost_usd if aggregate else Decimal(0),
        "mean_latency_ms": aggregate.mean_latency_ms if aggregate else 0.0,
    }


async def refresh(
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION
) -> dict[str, Any]:
    """Recompute and store. Called when a game reaches a terminal state, and by a stale read.

    Upserted on `prompt_version`, so a run never accumulates rows and switching prompt versions
    keeps both rather than thrashing between them.
    """
    payload = await build(session, prompt_version=prompt_version)
    mark = await fingerprint(session, prompt_version=prompt_version)

    statement = pg_insert(LeaderboardSnapshot).values(
        prompt_version=prompt_version or "",
        fingerprint=mark,
        payload=payload,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[LeaderboardSnapshot.prompt_version],
            set_={"fingerprint": mark, "payload": payload, "computed_at": sa.func.now()},
        )
    )
    return payload


async def current(
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION
) -> dict[str, Any]:
    """The stored run, rebuilt first if it does not match the games behind it.

    **Never serves a row whose fingerprint disagrees.** That is the whole bargain: the ranking is
    allowed to be stored precisely because a stored value that stopped matching its games would be
    caught here rather than published.
    """
    stored = await session.scalar(
        sa.select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.prompt_version == (prompt_version or "")
        )
    )
    mark = await fingerprint(session, prompt_version=prompt_version)

    if stored is not None and stored.fingerprint == mark:
        return dict(stored.payload)

    return await refresh(session, prompt_version=prompt_version)
