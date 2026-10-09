"""Stored rating runs — the leaderboard's and each pool's — and how they are kept honest
(ADR-0032, ADR-0061).

`service.py` computes the ranking; this decides **when**. The answer is: when the games behind it
change, not when a page is read.

**Every rating the site shows or acts on comes from here.** The leaderboard did; a pool's standings
and the matchmaker each fitted their own on every call, which was three paths to one number and
three chances for them to disagree. A run is keyed by prompt version and *scope* — `""` for the
leaderboard, `tournament:<id>` for one pool's own games — and every scope is kept honest the same
way.

The leaderboard was rebuilt from raw rows on every request, and four pages await it — two of which
display no rating at all. `/methodology` renders three scalars and paid for a full rating run to
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

import logging
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import DECISION_VERSION
from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.bench.bradley_terry import Rating
from chessmark.bench.service import (
    TERMINAL,
    Aggregate,
    Contestant,
    compute_aggregates,
    compute_ratings,
    contestant_label,
    ratings_by_key,
    scan,
)
from chessmark.db.models import Game, LeaderboardSnapshot, ModelEndpoint, TournamentGame

log = logging.getLogger(__name__)

#: Which engine produced a stored run. Part of the fingerprint, so changing the engine invalidates
#: every stored run on its first read instead of serving the old method's numbers as current.
#: Bump it whenever a change to `bench/bradley_terry.py` would move a published number — or, as for
#: `+effort` (ADR-0067), whenever the contestant key changes, since a stored run keyed the old way
#: would be read under the new.
RATING_METHOD = "bt-2draws+effort"

#: The leaderboard's scope: every counted game.
LEADERBOARD = ""


def pool_scope(tournament_id: uuid.UUID) -> str:
    """One pool's scope: that pool's games alone (ADR-0027)."""
    return f"tournament:{tournament_id}"


async def fingerprint(
    session: AsyncSession,
    *,
    prompt_version: str | None,
    tournament_id: uuid.UUID | None = None,
) -> str:
    """What a run was computed from, in two cheap indexed aggregates.

    Covers the inputs that can change a *number*:

    * the terminal games — their count and the latest ending, which together move whenever a game
      finishes, is aborted, or is deleted;
    * `model_endpoints` — because the precision an endpoint serves is half a contestant's identity
      (ADR-0015), so an edit there can move a row from `model@fp8` to `model@fp4`;
    * the prompt version, since a game played under an older prompt measured a different task;
    * the decision harness's version, for the same reason on the other harness (ADR-0049) — a bump
      retires decision games from the ratings, and a stored run that did not notice would go on
      counting them;
    * the rating method (ADR-0060). The games did not change when the engine did, so without it a
      run computed by Glicko-2 would match every other input and go on being served as current.

    For a pool the games are that pool's, so a game finishing in another event does not rebuild it.

    Deliberately **not** covered: `model_registry.display_name`. It is a label, resolved at read,
    and baking it in would force a rebuild for a cosmetic rename.
    """
    terminal = sa.select(sa.func.count(), sa.func.max(Game.ended_at)).where(
        Game.status.in_(TERMINAL)
    )
    if tournament_id is not None:
        terminal = terminal.join(TournamentGame, TournamentGame.game_id == Game.id).where(
            TournamentGame.tournament_id == tournament_id
        )
    games = (await session.execute(terminal)).one()
    endpoints = (
        await session.execute(
            sa.select(sa.func.count(), sa.func.max(ModelEndpoint.id)).select_from(ModelEndpoint)
        )
    ).one()

    return (
        f"{RATING_METHOD}|{prompt_version}|{DECISION_VERSION}|games={games[0]}@{games[1]}"
        f"|endpoints={endpoints[0]}@{endpoints[1]}"
    )


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
            effort = (player.sampling or {}).get("effort")
            key = contestant_label(
                slug, quantizations.get(player.id, "unknown"), str(effort) if effort else None
            )
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
        "games_by_contestant": counted,
    }
    return dict(_jsonable(payload))


def _row(contestant: Contestant, rating: Rating, aggregate: Aggregate | None) -> dict[str, Any]:
    """One contestant's numbers. No `display_name` — that is resolved at read."""
    return {
        "model_id": contestant.model_id,
        "model_slug": contestant.model_slug,
        "quantization": contestant.quantization,
        "effort": contestant.effort,
        "rating": rating.rating,
        "rating_deviation": rating.rd,
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
    """Recompute the leaderboard's run and store it. Called by a stale read."""
    mark = await fingerprint(session, prompt_version=prompt_version)
    payload = await build(session, prompt_version=prompt_version)
    await _store(session, prompt_version, LEADERBOARD, mark, payload)
    return payload


async def current(
    session: AsyncSession, *, prompt_version: str | None = PROMPT_VERSION
) -> dict[str, Any]:
    """The leaderboard's stored run, rebuilt first if it does not match the games behind it.

    **Never serves a row whose fingerprint disagrees.** That is the whole bargain: the ranking is
    allowed to be stored precisely because a stored value that stopped matching its games would be
    caught here rather than published.
    """
    stored = await _stored(session, prompt_version, LEADERBOARD)
    mark = await fingerprint(session, prompt_version=prompt_version)
    if stored is not None and stored.fingerprint == mark:
        return dict(stored.payload)
    return await refresh(session, prompt_version=prompt_version)


async def pool_ratings(
    session: AsyncSession,
    *,
    tournament_id: uuid.UUID,
    prompt_version: str | None = PROMPT_VERSION,
) -> dict[str, Rating]:
    """One pool's ratings over its own games, keyed by entrant, from its stored run (ADR-0061).

    The same bargain as `current`: read, check the fingerprint, rebuild only if it disagrees. What
    is stored is the rating and its deviation; `provisional` and `proven` are derived from them on
    the way out, so neither can drift from the numbers it describes (ADR-0028).
    """
    scope = pool_scope(tournament_id)
    stored = await _stored(session, prompt_version, scope)
    mark = await fingerprint(session, prompt_version=prompt_version, tournament_id=tournament_id)
    if stored is not None and stored.fingerprint == mark:
        payload = dict(stored.payload)
    else:
        ratings = await ratings_by_key(
            session, tournament_id=tournament_id, prompt_version=prompt_version
        )
        payload = {
            "ratings": {
                key: {"rating": rating.rating, "rating_deviation": rating.rd}
                for key, rating in ratings.items()
            }
        }
        await _store(session, prompt_version, scope, mark, payload)
    return {
        key: Rating(rating=value["rating"], rd=value["rating_deviation"])
        for key, value in payload["ratings"].items()
    }


async def _stored(
    session: AsyncSession, prompt_version: str | None, scope: str
) -> LeaderboardSnapshot | None:
    row: LeaderboardSnapshot | None = await session.scalar(
        sa.select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.prompt_version == (prompt_version or ""),
            LeaderboardSnapshot.scope == scope,
        )
    )
    return row


async def _store(
    session: AsyncSession,
    prompt_version: str | None,
    scope: str,
    mark: str,
    payload: dict[str, Any],
) -> None:
    """Upsert on `(prompt_version, scope)`, **in a transaction of its own**, and commit it.

    Its own because of where it is called from. A request's session is never committed, so a run
    written through it was rolled back when the request ended — every read of the leaderboard
    rebuilt it, for as long as ADR-0032 had been live, and no test noticed because they all share
    one session and read their own uncommitted write. And the matchmaker calls this mid-tick, where
    committing the caller's session would commit half a tick. A cache write belongs to neither.

    **A failed write is logged, not raised.** The payload is already computed and correct; losing
    the cache costs the next reader a rebuild, while raising would cost this one the page.

    The fingerprint is taken **before** the build, by both callers. Taken after, a game that ended
    during the build would be in the mark and not in the run, and the stale run would then match.
    """
    statement = pg_insert(LeaderboardSnapshot).values(
        prompt_version=prompt_version or "",
        scope=scope,
        fingerprint=mark,
        payload=payload,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[LeaderboardSnapshot.prompt_version, LeaderboardSnapshot.scope],
        set_={"fingerprint": mark, "payload": payload, "computed_at": sa.func.now()},
    )
    try:
        async with AsyncSession(bind=session.bind, expire_on_commit=False) as writer:
            await writer.execute(statement)
            await writer.commit()
    except sa.exc.SQLAlchemyError:
        log.warning("could not store the %s rating run", scope or "leaderboard", exc_info=True)
