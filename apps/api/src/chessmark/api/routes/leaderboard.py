"""The leaderboard (BENCH-02, UI-05).

Computed on request rather than read from a cache. Ratings are a pure function of the games that
produced them, and recomputing a few hundred games costs milliseconds — far less than the cost of
serving a number that has quietly drifted from the games behind it. When the game count makes that
untrue, the fix is a cached run with a recorded input hash, not a mutable table.

The exclusions are part of the response. A leaderboard that silently drops a third of its games is
indistinguishable from one that is wrong.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.api.deps import SessionDep
from chessmark.api.routes.games import _served_by_many as served_by_many
from chessmark.api.schemas import (
    BenchSummary,
    ExcludedGame,
    GameSummary,
    Leaderboard,
    LeaderboardRow,
)
from chessmark.bench import snapshot
from chessmark.bench.service import TERMINAL
from chessmark.db.models import Game, ModelRegistry, Player

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", response_model=Leaderboard)
async def get_leaderboard(session: SessionDep) -> Leaderboard:
    """The ranking, read from the run stored when the last game ended (ADR-0032).

    No scan, no Glicko-2, no aggregates on a request. `snapshot.current` rebuilds first if the
    stored run does not match the games behind it, so this is never the stale answer — it is either
    the current one cheaply or the current one slowly.
    """
    stored = await snapshot.current(session, prompt_version=PROMPT_VERSION)

    # Names are resolved here rather than stored, so renaming a model does not need a rebuild and
    # cannot leave a snapshot showing a label the registry no longer uses.
    names = {row.id: row.display_name for row in await session.scalars(sa.select(ModelRegistry))}

    rows = [
        LeaderboardRow(
            **row,
            display_name=names.get(uuid.UUID(str(row["model_id"])), row["model_slug"]),
        )
        for row in stored["rows"]
    ]

    # Rating first, but a wide deviation is not a high rank — ties on rating go to whoever we are
    # more sure about.
    rows.sort(key=lambda row: (-row.rating, row.rating_deviation))

    return Leaderboard(
        rows=rows,
        games_counted=stored["games_counted"],
        excluded=[
            ExcludedGame(game_id=uuid.UUID(entry["game_id"]), reason=entry["reason"])
            for entry in stored["excluded"]
        ],
        prompt_version=PROMPT_VERSION,
        periods=stored["periods"],
    )


@router.get("/summary", response_model=BenchSummary)
async def get_summary(session: SessionDep) -> BenchSummary:
    """The counts, without the ranking.

    `/about` and `/methodology` show these and no rating. Fetching the whole leaderboard to print
    three integers is what put a Glicko-2 run on the critical path of a page of prose.

    Registered **before** `/{model_slug:path}/games` so the path converter cannot swallow it.
    """
    stored = await snapshot.current(session, prompt_version=PROMPT_VERSION)
    finished = await session.scalar(
        sa.select(sa.func.count()).select_from(Game).where(Game.status.in_(TERMINAL))
    )

    return BenchSummary(
        games_counted=stored["games_counted"],
        games_excluded=len(stored["excluded"]),
        games_finished=int(finished or 0),
        prompt_version=PROMPT_VERSION,
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
    stored = await snapshot.current(session, prompt_version=PROMPT_VERSION)

    # The counted set travels with the run that counted it, so the drill-down does not scan either.
    # Every published number has to be reachable from the games that produced it, and reaching them
    # through a *different* eligibility pass is how "the games behind this row" drifts from the row.
    wanted = [
        game_id
        for label, game_ids in stored["games_by_contestant"].items()
        for game_id in game_ids
        if label.split("@")[0] == model_slug
        and (quantization is None or label.split("@", 1)[1] == quantization)
    ]
    if not wanted:
        return []

    ids = [uuid.UUID(game_id) for game_id in wanted]
    games = list(
        await session.scalars(sa.select(Game).where(Game.id.in_(ids)).order_by(Game.created_at))
    )
    players = list(await session.scalars(sa.select(Player).where(Player.game_id.in_(ids))))
    by_game: dict[uuid.UUID, list[Player]] = {}
    for player in players:
        by_game.setdefault(player.game_id, []).append(player)

    served = await served_by_many(session, ids)

    return [
        GameSummary.from_model(game, by_game.get(game.id, []), served_by=served.get(game.id, {}))
        for game in games
    ]
