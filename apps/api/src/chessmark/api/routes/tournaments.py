"""Tournaments, read-only (BENCH-05, UI).

Public, like everything else that only reads (AUTH-02): a tournament is a spectacle, and the
whole point of running one is that people can watch it.

Standings are computed on request from the pairings, never stored. A tournament's table is a pure
function of its results, and a stored copy is a copy that can drift — the same reasoning the
leaderboard uses. Recomputing a few dozen games costs nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from chessmark.api.deps import SessionDep
from chessmark.api.routes.games import _served_by as served_by
from chessmark.api.schemas import (
    GameSummary,
    StandingOut,
    TournamentDetail,
    TournamentPairingOut,
    TournamentStats,
    TournamentSummary,
)
from chessmark.db import tournaments as repo
from chessmark.db.enums import GameStatus
from chessmark.db.models import (
    Game,
    Player,
    Tournament,
    TournamentEntrant,
    TournamentGame,
)
from chessmark.game import GameResult
from chessmark.tournament import standings as compute_standings

router = APIRouter(prefix="/tournaments", tags=["tournaments"])


#: What a game's own status says about its pairing. The game record is the authority (invariant 1).
_BY_STATUS = {
    GameStatus.RUNNING: "live",
    GameStatus.PENDING: "live",
    GameStatus.PAUSED: "paused",
    GameStatus.FINISHED: "played",
    GameStatus.ABORTED: "abandoned",
}


def _state(row: TournamentGame, status: GameStatus | None = None) -> str:
    """Derived, not stored, so the page describes reality rather than a scheduler's last word.

    **The game decides, and the pairing only fills in where there is no game.** It was the other
    way round — `abandoned_reason` first, then `white_score`, then the status — and both of those
    columns are a *verdict already recorded*, which a resumed game invalidates. Two contradictions
    reached the page at once from that ordering:

    * Four pairings kept the score of a forfeit that had just been overturned, so games running at
      up to ply 89 were drawn as **played** and the event reported `live: 0` while four boards
      moved. (`resume_game.py` now clears the score; this is the half that stops a stale one from
      being believed.)
    * A game abandoned on a provider 404, resumed, and played on to checkmate at ply 120 was still
      drawn as **abandoned**, because its pairing's `abandoned_reason` outranked its own result.

    **`paused` is its own state, and used not to be.** Any pairing holding a game with no score was
    reported "live", so a page said "4 live" while some of those games were sitting on a provider
    cooldown with nothing happening (ADR-0017) — and a `pending` game, created but not yet picked
    up, counted as live too. The scheduler's word was "there is a game here"; the reader's question
    is "is anything happening".
    """
    if row.game_id is not None and status is not None:
        return _BY_STATUS.get(status, "live")
    # No game, or one we could not read: the pairing's own record is all there is.
    if row.abandoned_reason:
        return "abandoned"
    if row.white_score is not None:
        return "played"
    return "waiting"


async def _stats(session: SessionDep, tournament_id: uuid.UUID) -> TournamentStats:
    pairs = (
        await session.execute(
            sa.select(TournamentGame, Game.status)
            .outerjoin(Game, Game.id == TournamentGame.game_id)
            .where(TournamentGame.tournament_id == tournament_id)
        )
    ).all()
    rows = [row for row, _ in pairs]
    states = [_state(row, status) for row, status in pairs]

    totals = (
        await session.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(Game.total_cost_usd), 0),
                sa.func.coalesce(sa.func.sum(Game.total_tokens), 0),
                sa.func.coalesce(sa.func.sum(Game.ply_count), 0),
                sa.func.count(Game.id).filter(Game.status == GameStatus.FINISHED),
                # Only finished games have a result. A live one carries `ongoing`, which is not
                # `draw` — so counting "not a draw" reported a decisive result for a game still
                # being played.
                sa.func.count(Game.id).filter(
                    Game.status == GameStatus.FINISHED,
                    Game.result.in_((GameResult.WHITE_WINS, GameResult.BLACK_WINS)),
                ),
                sa.func.count(Game.id).filter(
                    Game.status == GameStatus.FINISHED, Game.result == GameResult.DRAW
                ),
            )
            .select_from(TournamentGame)
            .join(Game, Game.id == TournamentGame.game_id)
            .where(TournamentGame.tournament_id == tournament_id)
        )
    ).one()
    cost, tokens, plies, finished, decisive, draws = totals

    illegal = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Player.illegal_attempts), 0))
        .select_from(TournamentGame)
        .join(Player, Player.game_id == TournamentGame.game_id)
        .where(TournamentGame.tournament_id == tournament_id)
    )

    return TournamentStats(
        pairings=len(rows),
        played=states.count("played"),
        live=states.count("live"),
        paused=states.count("paused"),
        waiting=states.count("waiting"),
        abandoned=states.count("abandoned"),
        total_cost_usd=cost,
        total_tokens=int(tokens or 0),
        total_plies=int(plies or 0),
        # Averaged over finished games only: a game two moves in would drag it down.
        mean_plies=(float(plies) / finished) if finished else None,
        illegal_attempts=int(illegal or 0),
        decisive=int(decisive or 0),
        draws=int(draws or 0),
    )


def _summary_fields(
    tournament: Tournament, entrants: int, stats: TournamentStats
) -> dict[str, Any]:
    return {
        "id": tournament.id,
        "slug": tournament.slug,
        "name": tournament.name,
        "status": str(tournament.status),
        "format": tournament.format,
        "double": tournament.double,
        "rounds": tournament.rounds,
        "is_ranked": tournament.is_ranked,
        "max_concurrent": tournament.max_concurrent,
        "max_usd": tournament.max_usd,
        "entrant_count": entrants,
        "field_description": str(tournament.field_filter.get("describes") or "the whole field"),
        "created_at": tournament.created_at,
        "started_at": tournament.started_at,
        "ended_at": tournament.ended_at,
        "stats": stats,
    }


@router.get("", response_model=list[TournamentSummary])
async def list_tournaments(
    session: SessionDep, limit: int = Query(default=20, ge=1, le=100)
) -> list[TournamentSummary]:
    rows = list(
        await session.scalars(
            sa.select(Tournament).order_by(Tournament.created_at.desc()).limit(limit)
        )
    )

    summaries: list[TournamentSummary] = []
    for tournament in rows:
        entrants = await session.scalar(
            sa.select(sa.func.count(TournamentEntrant.id)).where(
                TournamentEntrant.tournament_id == tournament.id
            )
        )
        stats = await _stats(session, tournament.id)
        summaries.append(
            TournamentSummary(**_summary_fields(tournament, int(entrants or 0), stats))
        )
    return summaries


@router.get("/{slug}", response_model=TournamentDetail)
async def get_tournament(session: SessionDep, slug: str) -> TournamentDetail:
    """One tournament: its table, every pairing, and the games behind them."""
    tournament = await session.scalar(sa.select(Tournament).where(Tournament.slug == slug))
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No tournament {slug!r} — it may never have been created.",
        )

    entrant_rows = list(
        await session.scalars(
            sa.select(TournamentEntrant)
            .where(TournamentEntrant.tournament_id == tournament.id)
            .order_by(TournamentEntrant.seed, TournamentEntrant.key)
        )
    )
    names = {row.key: row.display_name for row in entrant_rows}

    entrants = await repo.entrants_of(session, tournament.id)
    results = await repo.results_so_far(session, tournament.id)
    table = compute_standings(entrants, results)

    pairing_pairs = (
        await session.execute(
            sa.select(TournamentGame, Game.status)
            .outerjoin(Game, Game.id == TournamentGame.game_id)
            .where(TournamentGame.tournament_id == tournament.id)
            .order_by(TournamentGame.round_number, TournamentGame.id)
        )
    ).all()
    pairing_rows = [row for row, _ in pairing_pairs]
    pairing_status = {row.id: status for row, status in pairing_pairs}

    game_ids = [row.game_id for row in pairing_rows if row.game_id]
    games: list[GameSummary] = []
    if game_ids:
        rows = list(await session.scalars(sa.select(Game).where(Game.id.in_(game_ids))))
        players = list(await session.scalars(sa.select(Player).where(Player.game_id.in_(game_ids))))
        by_game: dict[uuid.UUID, list[Player]] = {}
        for player in players:
            by_game.setdefault(player.game_id, []).append(player)
        games = [
            GameSummary.from_model(
                game,
                by_game.get(game.id, []),
                served_by=await served_by(session, game.id),
            )
            for game in rows
        ]

    stats = await _stats(session, tournament.id)
    return TournamentDetail(
        **_summary_fields(tournament, len(entrant_rows), stats),
        standings=[
            StandingOut(
                place=s.place,
                key=s.key,
                display_name=names.get(s.key, s.key),
                seed=s.entrant.seed,
                played=s.played,
                wins=s.wins,
                draws=s.draws,
                losses=s.losses,
                byes=s.byes,
                score=s.score,
                sonneborn_berger=s.sonneborn_berger,
            )
            for s in table
        ],
        pairings=[
            TournamentPairingOut(
                id=row.id,
                round_number=row.round_number,
                white_key=row.white_key,
                black_key=row.black_key,
                white_score=row.white_score,
                state=_state(row, pairing_status.get(row.id)),
                game_id=row.game_id,
                abandoned_reason=row.abandoned_reason,
                started_at=row.started_at,
                ended_at=row.ended_at,
            )
            for row in pairing_rows
        ],
        games=games,
    )
