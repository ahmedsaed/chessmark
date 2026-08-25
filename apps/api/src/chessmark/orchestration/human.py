"""Actions a person takes in a game (HUMAN-01 → HUMAN-06).

The model's side of a game runs through `TurnRunner`, which owns a transcript, a token budget and
a tool loop. A human has none of those: they have a move, and the same referee.

Everything here is deliberately shaped like the worker's turn — validate against a rebuilt
referee, append exactly one `game_events` row per state change (invariant 7), and let the caller
commit and *then* enqueue. The server is the only authority on the board (invariant 1), so a
crafted request that bypasses the client meets exactly the same `Referee.play` the models do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import prompts, transcript
from chessmark.db.enums import EventType, GameStatus, ModerationStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Message, Player
from chessmark.db.repositories import (
    append_event,
    finish_game,
    rebuild_referee,
    record_ply,
)
from chessmark.game import Colour, IllegalMoveError, Outcome, Referee, Termination

#: A human message is capped before it reaches a model's transcript. Not a moderation control —
#: that is Phase 11 — but a bound on how much untrusted text can enter a prompt at once.
MAX_MESSAGE_CHARS = 500


class NotYourTurnError(Exception):
    """The caller tried to act when it was not their move, or in a game that is not running."""


class NotYourGameError(Exception):
    """The caller does not hold a seat in this game."""


class StalePlyError(Exception):
    """The client's view of the position is behind the server's.

    Raised rather than silently accepted so a double-submitted move — a double click, a retried
    request — cannot play two moves. The same guarantee `expected_ply` gives the queue (ADR-0007),
    offered to the client.
    """


@dataclass(slots=True)
class HumanAction:
    """What the caller must do after committing.

    The enqueue deliberately does not happen in here. It has to follow the commit, or a worker can
    be handed a ply that a rolled-back transaction means never existed.
    """

    ply: int
    outcome: Outcome | None = None
    #: Event sequence before the action, so the caller can publish exactly what it appended.
    before_seq: int = 0
    detail: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def game_over(self) -> bool:
        return self.outcome is not None


async def seat_of(session: AsyncSession, game_id: uuid.UUID, user_id: uuid.UUID) -> Player:
    """The human seat this user holds, or `NotYourGameError`.

    Spectating is open to everyone (AUTH-02); *acting* requires the seat. Checked by user id
    rather than by "is there a human seat", so one person cannot move for another.
    """
    player = await session.scalar(
        sa.select(Player).where(
            Player.game_id == game_id,
            Player.user_id == user_id,
            Player.kind == PlayerKind.HUMAN,
        )
    )
    if player is None:
        raise NotYourGameError("You do not hold a seat in this game.")
    return player


def _require_turn(game: Game, player: Player, referee: Referee) -> Colour:
    if game.status is not GameStatus.RUNNING:
        raise NotYourTurnError(f"This game is {game.status.value}.")
    if referee.is_over:
        raise NotYourTurnError("This game is already over.")

    colour = Colour(player.colour)
    if referee.side_to_move is not colour:
        raise NotYourTurnError("It is not your move.")
    return colour


async def play_move(
    session: AsyncSession,
    *,
    game: Game,
    player: Player,
    move_text: str,
    expected_ply: int | None = None,
) -> HumanAction:
    """Validate and commit one human half-move.

    `move_text` is whatever the person sent — SAN or UCI. It is never trusted: the referee decides,
    and an illegal move raises rather than being recorded. Unlike a model, a human gets no retry
    budget and is never forfeited for trying something illegal; the move is simply refused and it
    stays their turn. Forfeiting a person for a mis-drag would be absurd, and the illegal-move rate
    is a measurement of *models* (ADR-0002).
    """
    referee = await rebuild_referee(session, game)

    # Checked *before* whose turn it is. A resubmitted move — a double click, a retried request —
    # arrives after the opponent has been handed the move, so the turn check would fire first and
    # report "it is not your move", which is true but useless: it hides the fact that the client
    # is simply behind, and it is the same message a genuine out-of-turn attempt gets.
    if expected_ply is not None and expected_ply != referee.ply:
        raise StalePlyError(
            f"The game is at ply {referee.ply}, not {expected_ply}. Reload before moving again."
        )

    colour = _require_turn(game, player, referee)

    before_seq = game.event_seq
    move = referee.play(move_text)  # raises IllegalMoveError; the caller turns it into a 422

    await record_ply(session, game_id=game.id, colour=colour, move=move, turn_id=None)
    await append_event(
        session,
        game_id=game.id,
        type=EventType.MOVE_MADE,
        payload={
            "player_id": str(player.id),
            "colour": colour.value,
            "ply": move.ply,
            "san": move.move.san,
            "uci": move.move.uci,
            "fen": move.fen_after,
            "check": move.move.is_check,
            "human": True,
        },
    )

    outcome = referee.outcome if referee.is_over else None
    if outcome is not None:
        await _conclude(session, game, outcome)

    return HumanAction(ply=move.ply, outcome=outcome, before_seq=before_seq, detail=move.move.san)


async def resign(session: AsyncSession, *, game: Game, player: Player) -> HumanAction:
    """Resign. Final, and available whether or not it is your turn."""
    referee = await rebuild_referee(session, game)
    if game.status is not GameStatus.RUNNING or referee.is_over:
        raise NotYourTurnError("This game is already over.")

    before_seq = game.event_seq
    outcome = referee.resign(Colour(player.colour))
    await _conclude(session, game, outcome)
    return HumanAction(
        ply=referee.ply, outcome=outcome, before_seq=before_seq, detail=outcome.detail
    )


async def offer_draw(session: AsyncSession, *, game: Game, player: Player) -> HumanAction:
    """Offer a draw.

    Advisory, exactly as it is between two models: the offer is recorded and the opponent answers
    on its own turn. Nothing about the position changes.
    """
    referee = await rebuild_referee(session, game)
    if game.status is not GameStatus.RUNNING or referee.is_over:
        raise NotYourTurnError("This game is already over.")

    before_seq = game.event_seq
    await append_event(
        session,
        game_id=game.id,
        type=EventType.DRAW_OFFERED,
        payload={
            "player_id": str(player.id),
            "colour": player.colour,
            "ply": referee.ply,
            "human": True,
        },
    )
    await _tell_opponent(session, game, player, prompts.DRAW_OFFER_RECEIVED)
    return HumanAction(ply=referee.ply, before_seq=before_seq, detail="draw offered")


async def open_draw_offer(
    session: AsyncSession, *, game: Game, referee: Referee
) -> uuid.UUID | None:
    """Whose draw offer is currently open, if any.

    An offer lapses the moment a move is played, as it does over a board: it is open only if the
    most recent draw offer was made at the position still on the board. Derived from `game_events`
    rather than stored on the game, because that log is already the one source live, reconnect and
    replay all read (ADR-0008) — a second copy could disagree with it.
    """
    row = await session.scalar(
        sa.select(GameEvent)
        .where(GameEvent.game_id == game.id, GameEvent.type == EventType.DRAW_OFFERED)
        .order_by(GameEvent.seq.desc())
        .limit(1)
    )
    if row is None:
        return None
    if int(row.payload.get("ply", -1)) != referee.ply:
        return None

    offered_by = row.payload.get("player_id")
    return uuid.UUID(str(offered_by)) if offered_by else None


async def respond_to_draw(
    session: AsyncSession, *, game: Game, player: Player, accept: bool
) -> HumanAction:
    """Accept or decline the opponent's open draw offer.

    This is the direction that can actually conclude a game. A model offers with the `offer_draw`
    tool it already has, and a person answers here — no tool-schema change, so ranked games keep
    the exact prompt and tool list they have always had (invariant 5).

    The reverse direction does not close: a model has no `accept_draw` tool, so a human's offer
    stays advisory. Recorded as a limitation rather than papered over.
    """
    referee = await rebuild_referee(session, game)
    if game.status is not GameStatus.RUNNING or referee.is_over:
        raise NotYourTurnError("This game is already over.")

    offered_by = await open_draw_offer(session, game=game, referee=referee)
    if offered_by is None:
        raise NotYourTurnError("There is no draw offer to answer.")
    if offered_by == player.id:
        raise NotYourTurnError("That is your own draw offer.")

    before_seq = game.event_seq

    if not accept:
        await _tell_opponent(session, game, player, prompts.DRAW_DECLINED)
        return HumanAction(ply=referee.ply, before_seq=before_seq, detail="draw declined")

    outcome = referee.agree_draw()
    await _conclude(session, game, outcome)
    return HumanAction(
        ply=referee.ply, outcome=outcome, before_seq=before_seq, detail=outcome.detail
    )


async def say(session: AsyncSession, *, game: Game, player: Player, message: str) -> HumanAction:
    """Send a message to the model (TALK-06).

    Reuses the channel a model's own `say` tool writes to, rather than inventing a second one: the
    text arrives as `Your opponent says: …`, in the same position in the transcript, so a model
    cannot tell a human apart from another model by the shape of the prompt. That is the point —
    a benchmark of adversarial reliability should not hand the model a tell.

    **This channel is not moderated.** The message is stored `PENDING` and delivered as written.
    Phase 11 is where a check before public display belongs, and this must be gated behind it
    before the site is public.
    """
    text = message.strip()
    if not text:
        raise ValueError("A message cannot be empty.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ValueError(f"A message is at most {MAX_MESSAGE_CHARS} characters.")

    referee = await rebuild_referee(session, game)
    if game.status is not GameStatus.RUNNING or referee.is_over:
        raise NotYourTurnError("This game is already over.")

    before_seq = game.event_seq
    session.add(
        Message(
            game_id=game.id,
            player_id=player.id,
            ply_number=referee.ply,
            content=text,
            moderation_status=ModerationStatus.PENDING,
        )
    )
    await append_event(
        session,
        game_id=game.id,
        type=EventType.MESSAGE_SENT,
        payload={
            "player_id": str(player.id),
            "colour": player.colour,
            "ply": referee.ply,
            # `content`, matching what a model's `say` writes (`agents/turn.py`). This said
            # `message` until it was noticed that the panel reads `content` and therefore never
            # displayed a word a person typed — stored, delivered to the model, invisible on the
            # page. One key for one thing; the reader deserializes both (`lib/turns.ts`), because
            # the event log is append-only and the rows written under the old key still exist.
            "content": text,
            "human": True,
        },
    )
    await _tell_opponent(session, game, player, prompts.OPPONENT_SAID.format(message=text))
    return HumanAction(ply=referee.ply, before_seq=before_seq, detail=text)


async def _tell_opponent(session: AsyncSession, game: Game, player: Player, content: str) -> None:
    """Append to the model's transcript, if the opponent is a model.

    Appended immediately rather than at the start of the model's next turn, because the transcript
    is append-only and byte-stable (invariant 7 of the working guide): rows go on the end, in the
    order they happened, and are never rewritten.
    """
    opponent = await session.scalar(
        sa.select(Player).where(Player.game_id == game.id, Player.id != player.id)
    )
    if opponent is None or PlayerKind(opponent.kind) is not PlayerKind.MODEL:
        return

    await transcript.append_message(
        session,
        player_id=opponent.id,
        game_id=game.id,
        role="user",
        content=content,
    )


async def _conclude(session: AsyncSession, game: Game, outcome: Outcome) -> None:
    """Finish the game and append its single ending event.

    Mirrors the worker's `_conclude` deliberately: live, reconnect and replay all read the same
    one row (ADR-0008), so a human ending must look identical to a model's.
    """
    if game.status is GameStatus.FINISHED:
        return

    await finish_game(session, game_id=game.id, outcome=outcome)
    await append_event(
        session,
        game_id=game.id,
        type=EventType.GAME_ENDED,
        payload={
            "result": str(outcome.result),
            "termination": str(outcome.termination),
            "detail": outcome.detail,
            "winner": outcome.winner.value if outcome.winner else None,
            "ply_count": game.ply_count,
            "total_cost_usd": str(game.total_cost_usd),
        },
    )


__all__ = [
    "MAX_MESSAGE_CHARS",
    "HumanAction",
    "IllegalMoveError",
    "NotYourGameError",
    "NotYourTurnError",
    "StalePlyError",
    "Termination",
    "offer_draw",
    "open_draw_offer",
    "play_move",
    "resign",
    "respond_to_draw",
    "say",
    "seat_of",
]
