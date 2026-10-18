"""The facts a finished game's page states in prose, worked out once and stored (ADR-0069).

A game page's readable text was its move list and two model ids, which is why a search engine saw
four hundred of them as the same page. The digest is what makes each one about *its* game: the
opening it reached, the material each side ended with, and up to three moments in the models' own
words — the first illegal move, the biggest capture, and the last turn.

**Facts, not sentences.** The page writes the prose from these. Wording is presentation and
changes often; a stored sentence would need a backfill for every change of phrasing, while a
stored fact only needs one when what is *computed* changes, which is what `DIGEST_VERSION` marks.

**Stored, because the game page is read live.** `getGame` is not cached for a game page, so each
view is an API read, and the digest replays every move and reads every reasoning trace. A finished
game never changes, so it is computed on the first read after the game ends and read back from
`games.digest` after that. A stored digest older than `DIGEST_VERSION` is recomputed the same way,
and `scripts/backfill_digests.py` does the whole archive ahead of a deploy.

**Only for a game that is over.** Reasoning is never shown to a person mid-game (invariant 8), and
a digest quotes it. A concluded game reveals everything (`api/redaction.py`), so the digest is
built for those and no others, and is never built for a game a person might still be playing.

**Quoted, never summarised** (invariant 3). An excerpt is a contiguous slice of what the model
wrote, marked as cut when it is, and the whole trace stays one click away in the replay.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import chess
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game, GameEvent, Ply
from chessmark.game.board import PIECE_VALUES
from chessmark.game.facts import PIECE_NAMES
from chessmark.game.openings import identify

log = logging.getLogger(__name__)

#: Bump when what is computed changes — not when the page's wording does. Every stored digest with
#: another version is recomputed on its next read.
DIGEST_VERSION = 1

#: Long enough to carry a model's reason for a move, short enough that three of them do not bury
#: the board. A model's trace for one move runs to thousands of characters; the page links to it.
EXCERPT_CHARS = 600

#: A capture worth calling a turning point: a minor piece or more. Trading pawns is not one.
MIN_TURNING_VALUE = 3

#: The events a digest reads. Tool calls are left out: their results carry the legal move list,
#: which is most of the bytes of a turn and none of what the page quotes.
_READ = (
    EventType.TURN_STARTED,
    EventType.THINKING,
    EventType.OUTPUT,
    EventType.ILLEGAL_ATTEMPT,
    EventType.MOVE_MADE,
)

_CONCLUDED = {GameStatus.FINISHED, GameStatus.ABORTED}

_WHITESPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?]\s")


@dataclass(slots=True)
class TurnText:
    """One turn's worth of what a model wrote and did, gathered from the event log."""

    ply: int | None
    colour: str | None
    reasoning: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    illegal: list[dict[str, Any]] = field(default_factory=list)
    move: str | None = None

    def text(self) -> tuple[str, str] | None:
        """The turn's reasoning, or its prose if it has none, and which of the two it was.

        Providers split differently: DeepSeek puts everything in reasoning, Gemini everything in
        content (`EventType.OUTPUT`). Preferring one and falling back keeps both readable.
        """
        for source, parts in (("reasoning", self.reasoning), ("output", self.output)):
            joined = " ".join(part for part in parts if part and part.strip())
            if joined.strip():
                return source, joined
        return None


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> tuple[str, bool]:
    """A contiguous slice of `text`, at most `limit` characters, and whether it was cut.

    **The end, not the start.** A trace opens by restating the position and closes on the move it
    chose and why; the close is what the page is quoting it for. The cut lands on a sentence start
    where there is one near it, so the quote does not open mid-word.
    """
    flat = _WHITESPACE.sub(" ", text).strip()
    if len(flat) <= limit:
        return flat, False
    tail = flat[-limit:]
    boundary = _SENTENCE_END.search(tail, 0, limit // 3)
    if boundary is not None:
        tail = tail[boundary.end() :]
    else:
        space = tail.find(" ")
        tail = tail[space + 1 :] if 0 <= space < limit // 3 else tail
    return tail.strip(), True


def turns_from_events(events: Sequence[tuple[EventType | str, dict[str, Any]]]) -> list[TurnText]:
    """The log, cut into turns at each `turn_started`. Events before the first belong to none."""
    turns: list[TurnText] = []
    current: TurnText | None = None
    for kind, payload in events:
        kind = EventType(kind)
        if kind == EventType.TURN_STARTED:
            current = TurnText(ply=payload.get("ply"), colour=payload.get("colour"))
            turns.append(current)
        elif current is None:
            continue
        elif kind == EventType.THINKING:
            current.reasoning.append(str(payload.get("reasoning") or ""))
        elif kind == EventType.OUTPUT:
            current.output.append(str(payload.get("content") or ""))
        elif kind == EventType.ILLEGAL_ATTEMPT:
            current.illegal.append({"move": payload.get("move"), "detail": payload.get("detail")})
        elif kind == EventType.MOVE_MADE:
            current.move = payload.get("san")
    return turns


@dataclass(frozen=True, slots=True)
class PlyRow:
    ply_number: int
    colour: str
    san: str
    uci: str
    fen_before: str
    fen_after: str


def _captured(row: PlyRow) -> tuple[str, int] | None:
    """The piece this ply took and its value, including the pawn an en-passant capture removes."""
    board = chess.Board(row.fen_before)
    move = chess.Move.from_uci(row.uci)
    if board.is_en_passant(move):
        return PIECE_NAMES[chess.PAWN], PIECE_VALUES[chess.PAWN]
    piece = board.piece_at(move.to_square)
    if piece is None or piece.piece_type == chess.KING:
        return None
    return PIECE_NAMES[piece.piece_type], PIECE_VALUES[piece.piece_type]


def _material(fen: str) -> dict[str, int]:
    board = chess.Board(fen)
    return {
        name: sum(
            len(board.pieces(piece_type, colour)) * value
            for piece_type, value in PIECE_VALUES.items()
        )
        for name, colour in (("white", chess.WHITE), ("black", chess.BLACK))
    }


def _moment(kind: str, turn: TurnText, **extra: Any) -> dict[str, Any]:
    quoted = turn.text()
    words, cut = excerpt(quoted[1]) if quoted else (None, False)
    return {
        "kind": kind,
        "ply": turn.ply,
        "colour": turn.colour,
        "san": turn.move,
        "excerpt": words,
        "source": quoted[0] if quoted else None,
        "truncated": cut,
        **extra,
    }


def build(*, start_fen: str, plies: Sequence[PlyRow], turns: Sequence[TurnText]) -> dict[str, Any]:
    """The digest of one game, from its record. Pure: everything it reads is passed in."""
    opening = identify([row.san for row in plies], start_fen=start_fen)
    by_ply = {turn.ply: turn for turn in turns if turn.ply is not None and turn.move is not None}

    moments: list[dict[str, Any]] = []
    taken: set[int | None] = set()

    first_illegal = next((turn for turn in turns if turn.illegal), None)
    if first_illegal is not None:
        attempt = first_illegal.illegal[0]
        moments.append(
            _moment(
                "illegal",
                first_illegal,
                attempted=attempt.get("move"),
                detail=attempt.get("detail"),
            )
        )
        taken.add(first_illegal.ply)

    # The biggest capture before the last move, earliest among equals. The last move is the
    # `final` moment already, and a mate that also takes a piece should not be quoted twice.
    best: tuple[int, PlyRow, str] | None = None
    for row in plies[:-1]:
        captured = _captured(row)
        if captured is None:
            continue
        name, value = captured
        if value >= MIN_TURNING_VALUE and (best is None or value > best[0]):
            best = (value, row, name)
    if best is not None:
        value, row, name = best
        turn = by_ply.get(row.ply_number) or TurnText(
            ply=row.ply_number, colour=row.colour, move=row.san
        )
        if turn.ply not in taken:
            moments.append(_moment("capture", turn, captured=name, value=value))
            taken.add(turn.ply)

    # The last turn anyone took: the mating move, the resignation, the forfeit, the move that
    # repeated the position. Whichever it was, it is how the game ended. A person's seat logs no
    # turn, so when the last *ply* is newer than the last logged turn, the ply is the ending and
    # there is nothing to quote — quoting the model's earlier turn would put its words on a move it
    # did not make.
    last: TurnText | None = None
    newest = plies[-1] if plies else None
    if turns and (newest is None or (turns[-1].ply or 0) >= newest.ply_number):
        last = turns[-1]
    elif newest is not None:
        last = by_ply.get(newest.ply_number) or TurnText(
            ply=newest.ply_number, colour=newest.colour, move=newest.san
        )
    if last is not None and (last.ply is None or last.ply not in taken):
        moments.append(_moment("final", last))

    moments.sort(key=lambda moment: (moment["ply"] is None, moment["ply"] or 0))

    return {
        "version": DIGEST_VERSION,
        "opening": (
            {"eco": opening.eco, "name": opening.name, "ply": opening.ply} if opening else None
        ),
        "material": _material(plies[-1].fen_after if plies else start_fen),
        "moments": moments,
    }


async def compute(session: AsyncSession, game: Game) -> dict[str, Any]:
    """Read what `build` needs: two statements, however long the game was."""
    plies = [
        PlyRow(
            ply_number=n, colour=str(colour), san=san, uci=uci, fen_before=before, fen_after=after
        )
        for n, colour, san, uci, before, after in (
            await session.execute(
                sa.select(
                    Ply.ply_number, Ply.colour, Ply.san, Ply.uci, Ply.fen_before, Ply.fen_after
                )
                .where(Ply.game_id == game.id)
                .order_by(Ply.ply_number)
            )
        ).all()
    ]
    events = (
        await session.execute(
            sa.select(GameEvent.type, GameEvent.payload)
            .where(GameEvent.game_id == game.id, GameEvent.type.in_(_READ))
            .order_by(GameEvent.seq)
        )
    ).all()
    return build(
        start_fen=game.start_fen,
        plies=plies,
        turns=turns_from_events([(kind, payload) for kind, payload in events]),
    )


async def digest_for(session: AsyncSession, game: Game) -> dict[str, Any] | None:
    """The stored digest, computing and storing it first if it is missing or out of date.

    None for a game that is not over — see the module docstring for why that is a rule and not an
    optimisation.
    """
    if game.status not in _CONCLUDED:
        return None
    stored: dict[str, Any] | None = await session.scalar(
        sa.select(Game.digest).where(Game.id == game.id)
    )
    if stored is not None and stored.get("version") == DIGEST_VERSION:
        return stored
    digest = await compute(session, game)
    await store(session, game.id, digest)
    return digest


async def store(session: AsyncSession, game_id: uuid.UUID, digest: dict[str, Any]) -> None:
    """Write one digest **in a transaction of its own**, and commit it.

    The same reasoning as `bench.snapshot._store`: a request's session is never committed, so a
    digest written through it would be rolled back with the request and recomputed on every view.

    **A failed write is logged, not raised**, and the lock wait is bounded. The digest is already
    computed and correct for this reader; losing the write costs the next reader a recompute,
    while raising — or waiting on a row the reconciler holds — would cost this one the page.
    """
    try:
        async with AsyncSession(bind=session.bind, expire_on_commit=False) as writer:
            await writer.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
            await writer.execute(sa.update(Game).where(Game.id == game_id).values(digest=digest))
            await writer.commit()
    except sa.exc.SQLAlchemyError:
        log.warning("could not store the digest of game %s", game_id, exc_info=True)
