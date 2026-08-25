"""The leaderboard (BENCH-02, UI-05).

Computed on request rather than read from a cache. Ratings are a pure function of the games that
produced them, and recomputing a few hundred games costs milliseconds — far less than the cost of
serving a number that has quietly drifted from the games behind it. When the game count makes that
untrue, the fix is a cached run with a recorded input hash, not a mutable table.

The exclusions are part of the response. A leaderboard that silently drops a third of its games is
indistinguishable from one that is wrong.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.api.deps import SessionDep
from chessmark.api.routes.games import _served_by as served_by
from chessmark.api.schemas import (
    ExcludedGame,
    GameSummary,
    Leaderboard,
    LeaderboardRow,
)
from chessmark.bench.service import compute_aggregates, compute_ratings, ratable_games
from chessmark.db.models import ModelRegistry

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", response_model=Leaderboard)
async def get_leaderboard(session: SessionDep) -> Leaderboard:
    run = await compute_ratings(session, prompt_version=PROMPT_VERSION)
    aggregates = await compute_aggregates(session, prompt_version=PROMPT_VERSION)

    names = {row.id: row.display_name for row in await session.scalars(sa.select(ModelRegistry))}

    rows = [
        LeaderboardRow.from_rating(
            contestant,
            rating,
            aggregates.get(contestant),
            display_name=names.get(contestant.model_id),
        )
        for contestant, rating in run.ratings.items()
    ]

    # Rating first, but a wide deviation is not a high rank — ties on rating go to whoever we are
    # more sure about.
    rows.sort(key=lambda row: (-row.rating, row.rating_deviation))

    return Leaderboard(
        rows=rows,
        games_counted=run.games_counted,
        excluded=[ExcludedGame(game_id=e.game_id, reason=e.reason) for e in run.excluded],
        prompt_version=PROMPT_VERSION,
        periods=len(run.periods),
    )


@router.get("/{model_slug:path}/games", response_model=list[GameSummary])
async def get_contestant_games(
    session: SessionDep,
    model_slug: str,
    quantization: str | None = None,
) -> list[GameSummary]:
    """The games behind one leaderboard row (BENCH-02).

    Every published number has to be reachable from the games that produced it, or the ranking is
    asking to be taken on faith. Filtered to the *ratable* games only, so this is exactly what moved
    the rating — not every game the model has ever played.
    """
    summaries: list[GameSummary] = []

    for game, players, quantizations in await ratable_games(session, prompt_version=PROMPT_VERSION):
        for player in players:
            slug = str((player.sampling or {}).get("model") or "")
            if slug != model_slug:
                continue
            if quantization and quantizations.get(player.id, "unknown") != quantization:
                continue
            summaries.append(
                GameSummary.from_model(game, players, served_by=await served_by(session, game.id))
            )
            break

    return summaries
