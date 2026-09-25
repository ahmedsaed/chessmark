"""The game archive's query: every filter `GET /games` accepts, as one statement.

Kept apart from the route because it is the part with rules in it. Each filter is a `WHERE` on the
games table or an `EXISTS` over its seats — never a join — so a game appears once however many of
its seats match, and the page of games is always exactly one `SELECT` however many filters are on.
The seats are read afterwards in one batch; the endpoint costs two statements whatever it is asked.

**Paging is keyset, and the cursor is a game's id.** "Older than this game" is resolved by the
database against the anchor's own sort value, so the client never has to know which column a sort
uses or how to format a timestamp or a decimal — and a page does not shift underneath the reader
when a new game starts, which is what an `OFFSET` does on an archive that grows while it is read.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import aliased

from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import Game, ModelRegistry, Player, Tournament, TournamentGame
from chessmark.game import GameResult, Termination


class ArchiveSort(StrEnum):
    NEWEST = "newest"
    LONGEST = "longest"
    COSTLIEST = "costliest"


class ArchiveOutcome(StrEnum):
    """A result, spelled for a URL. `1/2-1/2` is PGN's, and not something to put in a query."""

    WHITE = "white"
    BLACK = "black"
    DRAW = "draw"


class ArchiveKind(StrEnum):
    #: Both seats held by a model — the benchmark proper.
    MODELS = "models"
    #: A person held a seat.
    HUMANS = "humans"


_RESULT = {
    ArchiveOutcome.WHITE: GameResult.WHITE_WINS,
    ArchiveOutcome.BLACK: GameResult.BLACK_WINS,
    ArchiveOutcome.DRAW: GameResult.DRAW,
}

_SORT_COLUMN: dict[ArchiveSort, Any] = {
    ArchiveSort.NEWEST: Game.created_at,
    ArchiveSort.LONGEST: Game.ply_count,
    ArchiveSort.COSTLIEST: Game.total_cost_usd,
}


def _like(needle: str) -> str:
    """A substring pattern with the caller's own `%` and `_` taken literally.

    Unescaped, a search for `50%` or `gpt_4` matched far more than it said — `_` is a wildcard,
    and a model id is full of them.
    """
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _seat_of(model: str) -> sa.Exists:
    """This game has a seat held by `model` (an OpenRouter id)."""
    return sa.exists().where(
        Player.game_id == Game.id,
        Player.model_id == ModelRegistry.id,
        ModelRegistry.openrouter_id == model,
    )


def archive_query(
    *,
    statuses: list[GameStatus] | None = None,
    outcome: ArchiveOutcome | None = None,
    termination: Termination | None = None,
    ranked: bool | None = None,
    kind: ArchiveKind | None = None,
    model: str | None = None,
    opponent: str | None = None,
    tournament: str | None = None,
    search: str | None = None,
    sort: ArchiveSort = ArchiveSort.NEWEST,
    before: uuid.UUID | None = None,
    after: uuid.UUID | None = None,
    limit: int = 50,
) -> sa.Select[tuple[Game]]:
    """The page of games matching every filter given, in `sort` order.

    `after` walks the other way — the page *newer* than a game — and comes back in ascending order;
    the caller reverses it. Asking for both is the caller's mistake and `before` wins.
    """
    column = _SORT_COLUMN[sort]
    query = sa.select(Game)

    if statuses:
        query = query.where(Game.status.in_(statuses))
    if outcome is not None:
        query = query.where(Game.result == _RESULT[outcome])
    if termination is not None:
        query = query.where(Game.termination == termination)
    if ranked is not None:
        query = query.where(Game.is_ranked.is_(ranked))

    if kind is ArchiveKind.HUMANS:
        query = query.where(
            sa.exists().where(Player.game_id == Game.id, Player.kind == PlayerKind.HUMAN)
        )
    elif kind is ArchiveKind.MODELS:
        # "No seat that is not a model" rather than "two model seats": the second is true of a
        # game whose rows are half-written, and the first cannot be.
        query = query.where(
            ~sa.exists().where(Player.game_id == Game.id, Player.kind != PlayerKind.MODEL)
        )

    if model is not None and opponent is not None:
        # **Two different seats.** Checked as two `EXISTS` this would call every game a model
        # played against anybody a match against *itself*, when `model == opponent`.
        mine, theirs = aliased(Player), aliased(Player)
        mine_model, their_model = aliased(ModelRegistry), aliased(ModelRegistry)
        query = query.where(
            sa.exists().where(
                mine.game_id == Game.id,
                theirs.game_id == Game.id,
                mine.id != theirs.id,
                mine.model_id == mine_model.id,
                theirs.model_id == their_model.id,
                mine_model.openrouter_id == model,
                their_model.openrouter_id == opponent,
            )
        )
    elif model is not None or opponent is not None:
        query = query.where(_seat_of(model or opponent or ""))

    if tournament is not None:
        query = query.where(
            sa.exists().where(
                TournamentGame.game_id == Game.id,
                TournamentGame.tournament_id == Tournament.id,
                Tournament.slug == tournament,
            )
        )

    if search:
        # A seat's display name — the model's name, or the name a person plays under — or the
        # OpenRouter id of the model in it, so `anthropic/` finds every Anthropic game even though
        # no display name says "anthropic". The name side is what `ix_players_display_name_trgm`
        # is for; the registry is a few hundred rows and needs nothing.
        pattern = _like(search)
        query = query.where(
            sa.exists().where(
                Player.game_id == Game.id,
                sa.or_(
                    Player.display_name.ilike(pattern, escape="\\"),
                    Player.model_id.in_(
                        sa.select(ModelRegistry.id).where(
                            sa.or_(
                                ModelRegistry.openrouter_id.ilike(pattern, escape="\\"),
                                ModelRegistry.display_name.ilike(pattern, escape="\\"),
                            )
                        )
                    ),
                ),
            )
        )

    anchor_id = before if before is not None else after
    if anchor_id is not None:
        anchor = aliased(Game)
        # `(value, id)` rather than the value alone: ply counts and costs tie constantly, and a
        # cursor on the value alone would skip every game sharing the anchor's.
        mine_key = sa.tuple_(column, Game.id)
        anchor_key = sa.tuple_(getattr(anchor, column.key), anchor.id)
        query = query.where(
            sa.exists().where(
                anchor.id == anchor_id,
                mine_key < anchor_key if before is not None else mine_key > anchor_key,
            )
        )

    if before is None and after is not None:
        return query.order_by(column.asc(), Game.id.asc()).limit(limit)
    return query.order_by(column.desc(), Game.id.desc()).limit(limit)
