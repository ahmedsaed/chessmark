"""The game the lobby shows when nothing is live, picked by the operator (ADR-0070).

The hero falls back to *some* finished game between live ones, and the most recent was often the
least interesting thing on the site: two free models shuffling kings to a draw. A featured game is
the operator saying which one instead, with a line of their own and a quote from each side.

**The quotes are the models' words, and that is checked, not trusted.** The operator types an
excerpt; it is accepted only if it is a contiguous slice of that side's own reasoning or notes in
that game, and what is stored is the slice of the record, not what was typed. A front page that
could put words in a model's mouth would be a summary wearing quotation marks (invariant 3), and the
whole trace stays one click away in the replay.

**Only a finished game, and only one no person sat in.** Reasoning is never shown mid-game
(invariant 8) and a finished game reveals everything, so a featured game is always over. A game
with a human seat is somebody's own game, and the front page is not where it gets published.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.digests import TurnText, turns_from_events
from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import FeaturedGame, Game, GameEvent, Player

#: The card sits under the site's own headline, beside a 440px board. Longer than this and the hero
#: grows past the board and pushes the rest of the lobby down for one sentence.
HEADLINE_CHARS = 160

#: Two of these sit side by side in that card. A quote is a line, not a trace — the replay has the
#: trace.
QUOTE_CHARS = 280

_WHITESPACE = re.compile(r"\s+")

#: Typographic marks a person cannot be expected to type the way the model wrote them. Matched as
#: their plain forms, one character for one, so a match still points at the record's own text.
_FOLD = str.maketrans({chr(0x2019): "'", chr(0x2018): "'", chr(0x201C): '"', chr(0x201D): '"'})

#: What a person wraps a quote in when copying it: double quotation marks, and the ellipsis that
#: says it was cut. Neither is the model's text. Single quotes are left alone — one at either end
#: is as likely to be the model's own apostrophe.
_WRAPPING = '"' + chr(0x201C) + chr(0x201D) + " "
_ELLIPSES = (chr(0x2026), "...")

_READ = (EventType.TURN_STARTED, EventType.THINKING, EventType.OUTPUT, EventType.MOVE_MADE)


class FeatureRefusedError(ValueError):
    """Why a game cannot be featured as asked. The message is written for the operator."""


@dataclass(frozen=True, slots=True)
class Pick:
    """What featuring a game asks for, before any of it is checked."""

    headline: str
    white_quote: str | None = None
    black_quote: str | None = None


def flatten(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _unwrap(text: str) -> str:
    """The quote without what a person added around it when copying it."""
    text = flatten(text)
    changed = True
    while changed:
        before = text
        text = text.strip(_WRAPPING)
        for mark in _ELLIPSES:
            text = text.removeprefix(mark).removesuffix(mark)
        changed = text != before
    return text


def find_quote(turns: list[TurnText], colour: str, asked: str) -> dict[str, Any]:
    """The record's own slice matching `asked`, from the last turn of `colour` that contains it.

    The last rather than the first because a model repeats itself across turns, and what a quote
    is usually *about* is late: the mate, the blunder before it.
    """
    wanted = _unwrap(asked)
    if not wanted:
        raise FeatureRefusedError(f"The {colour} quote is empty.")
    if len(wanted) > QUOTE_CHARS:
        raise FeatureRefusedError(
            f"The {colour} quote is {len(wanted)} characters; keep it under {QUOTE_CHARS}. "
            "Quote the line, not the trace — the replay has the trace."
        )

    folded = wanted.translate(_FOLD)
    for turn in reversed(turns):
        if turn.colour != colour:
            continue
        for source, parts in (("reasoning", turn.reasoning), ("output", turn.output)):
            text = flatten(" ".join(parts))
            at = text.translate(_FOLD).find(folded)
            if at >= 0:
                return {
                    "text": text[at : at + len(folded)],
                    "ply": turn.ply,
                    "san": turn.move,
                    "source": source,
                }
    raise FeatureRefusedError(
        f"{colour.capitalize()} never wrote that in this game. A quote has to be word for word "
        "from that side's reasoning or notes — copy it from the replay."
    )


async def _turns(session: AsyncSession, game_id: uuid.UUID) -> list[TurnText]:
    events = (
        await session.execute(
            sa.select(GameEvent.type, GameEvent.payload)
            .where(GameEvent.game_id == game_id, GameEvent.type.in_(_READ))
            .order_by(GameEvent.seq)
        )
    ).all()
    return turns_from_events([(kind, payload) for kind, payload in events])


async def feature(
    session: AsyncSession,
    game_id: uuid.UUID,
    pick: Pick,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> FeaturedGame:
    """Make `game_id` the lobby's pick, checking everything first. Does not commit."""
    game = await session.get(Game, game_id)
    if game is None:
        raise FeatureRefusedError("There is no game with that id.")
    if game.status != GameStatus.FINISHED:
        raise FeatureRefusedError(
            f"That game is {game.status}. Only a finished game can be featured: a live one "
            "would show its reasoning while it is still being played."
        )

    kinds = set(
        (await session.scalars(sa.select(Player.kind).where(Player.game_id == game_id))).all()
    )
    if PlayerKind.HUMAN in kinds:
        raise FeatureRefusedError(
            "A person played in that game. It is theirs, and the front page is not where it "
            "gets published."
        )

    headline = flatten(pick.headline)
    if not headline:
        raise FeatureRefusedError("A featured game needs a headline.")
    if len(headline) > HEADLINE_CHARS:
        raise FeatureRefusedError(
            f"The headline is {len(headline)} characters; keep it under {HEADLINE_CHARS}."
        )

    quotes: dict[str, dict[str, Any] | None] = {"white": None, "black": None}
    asked = {"white": pick.white_quote, "black": pick.black_quote}
    if any(asked.values()):
        turns = await _turns(session, game_id)
        for colour, text in asked.items():
            if text:
                quotes[colour] = find_quote(turns, colour, text)

    now = dt.datetime.now(dt.UTC)
    values = {
        "headline": headline,
        "white_quote": quotes["white"],
        "black_quote": quotes["black"],
        "featured_by_user_id": actor_user_id,
        "featured_at": now,
        "removed_at": None,
    }
    row_id = await session.scalar(
        insert(FeaturedGame)
        .values(id=uuid.uuid4(), game_id=game_id, **values)
        .on_conflict_do_update(index_elements=[FeaturedGame.game_id], set_=values)
        .returning(FeaturedGame.id)
    )
    featured = await session.get(FeaturedGame, row_id, populate_existing=True)
    assert featured is not None
    return featured


async def unfeature(session: AsyncSession, game_id: uuid.UUID) -> bool:
    """Take a game out of the hero. False when it was not featured. Does not commit."""
    result = await session.execute(
        sa.update(FeaturedGame)
        .where(FeaturedGame.game_id == game_id, FeaturedGame.removed_at.is_(None))
        .values(removed_at=dt.datetime.now(dt.UTC))
    )
    return bool(getattr(result, "rowcount", 0))


async def current(session: AsyncSession) -> FeaturedGame | None:
    """The pick the hero shows: the newest one still standing. One statement."""
    pick: FeaturedGame | None = await session.scalar(
        sa.select(FeaturedGame)
        .where(FeaturedGame.removed_at.is_(None))
        .order_by(FeaturedGame.featured_at.desc())
        .limit(1)
    )
    return pick


async def history(session: AsyncSession) -> list[FeaturedGame]:
    """Every pick, newest first, removed ones included."""
    return list(
        (
            await session.scalars(sa.select(FeaturedGame).order_by(FeaturedGame.featured_at.desc()))
        ).all()
    )
