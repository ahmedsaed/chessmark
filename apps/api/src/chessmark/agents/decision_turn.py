"""A decision model's turn: one request, one answer, one action (ADR-0049).

The counterpart of `turn.TurnRunner` for a seat whose model is asked through the Decisions API, and
far smaller, because most of what makes a chat turn hard does not exist here. There is no
transcript to grow or compact, no tool loop to bound, no prose to nudge, and no illegal move to
retry: the model is offered the legal moves and answers with one of them, and code checks that it
did before the referee sees it (invariant 1).

What it keeps from the chat turn is everything the rest of the system reads. One `Turn` row, one
`llm_calls` row holding the request and response verbatim (invariant 3), the spend from what the
provider reported (invariant 4), `turn_started` and `move_made` events shaped exactly as a chat
seat's are, and a `TurnResult` — so the worker pauses, retries and abandons a decision seat by the
same rules, and the pages render its moves with the code they already have.
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_request import (
    ACCEPT_DRAW_QUESTION,
    CLAIM_DRAW_QUESTION,
    FIFTY_MOVES,
    MOVE_QUESTION,
    OFFER_DRAW_QUESTION,
    RESIGN_QUESTION,
    THREEFOLD,
    DecisionRequest,
    build_request,
)
from chessmark.agents.decisions import Decision, DecisionGateway, MalformedDecisionError
from chessmark.agents.live import LiveChannel, NullLive
from chessmark.agents.live import block as live_block
from chessmark.agents.live import turn_started as live_turn
from chessmark.agents.sessions import session_for_game
from chessmark.agents.turn import TurnResult
from chessmark.agents.types import LlmError
from chessmark.db.enums import EventType, TurnStatus
from chessmark.db.models import Game, GameEvent, LlmCall, Player, Turn
from chessmark.db.repositories import append_event, open_draw_offer, record_ply
from chessmark.game import Colour, MoveOutcome, Referee

#: The gates, one per yes-or-no question, **and one for every model** (ADR-0049).
#:
#: Set from `scripts/probe_decisions.py`, not from a default — and the first default was wrong in a
#: way only a real game showed: asked whether a draw was "a fair result" from a "balanced" position,
#: both models sat at 0.3-0.5 from move one and agreed a draw at move seven of a level middlegame.
#: Reworded around winning chances, and with the facts cut back to what a board shows, the probe on
#: 2026-09-26 separated every labelled "no" from every labelled "yes", per question, for both models
#: (Kev's twelve positions over two runs — its host rate-limits on tokens per minute):
#:
#:                 kev-4b  no ≤  yes ≥    jev-1.13  no ≤  yes ≥
#:   resign               0.35   0.65               0.12   0.83
#:   claim_draw           0.34   0.86               0.07   0.89
#:   offer_draw           0.33   0.53               0.06   0.29
#:   accept_draw          0.30   0.53               0.07   0.37
#:
#: 0.5 sits between the two columns everywhere except Jev's dead-drawn rook ending, which it offers
#: and accepts at 0.29 and 0.37 — so Jev plays that ending on, and the game is drawn by the
#: fivefold or seventy-five-move rule instead: the same result, later. Lowering the gate to catch it
#: would sit inside Kev's "no" band, and a gate per model would be the harness tuning each
#: contestant's answers for it. One number, the plain reading of the question, re-probed whenever
#: `DECISION_VERSION` or the field of models changes.
#:
#: What a mistake costs, per gate: claiming or accepting a draw in a won position gives away half a
#: point, and declining one in a lost position loses the game it thought was lost; resigning a
#: saveable position loses it for good, and playing on in a lost one costs only time; offering
#: costs nothing by itself, since the opponent decides and the seat still moves.
CLAIM_DRAW_AT = 0.5
ACCEPT_DRAW_AT = 0.5
RESIGN_AT = 0.5
OFFER_DRAW_AT = 0.5


class DecisionTurnRunner:
    """Runs one decision-model turn to completion."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        gateway: DecisionGateway,
        referee: Referee,
        game: Game,
        player: Player,
        model: str,
        live: LiveChannel | None = None,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.referee = referee
        self.game = game
        self.player = player
        self.model = model
        self.live: LiveChannel = live or NullLive()
        self.colour = Colour(player.colour)

    async def run(self, resuming: Turn | None = None) -> TurnResult:
        """Play one turn.

        `resuming` is accepted so the worker can treat both runners alike, and it is only ever an
        `INTERRUPTED` chat turn's row — a decision turn is a single call and never keeps a partial
        round (`TurnResult.keep_rounds` stays false), so there is nothing of its own to resume.
        """
        turn = resuming
        if turn is None:
            turn = Turn(game_id=self.game.id, player_id=self.player.id, status=TurnStatus.RUNNING)
            self.session.add(turn)
            await self.session.flush()
        else:
            turn.status = TurnStatus.RUNNING

        result = TurnResult(turn_id=turn.id, status=TurnStatus.RUNNING)
        started = time.perf_counter()
        ply = self.referee.ply + 1

        if resuming is None:
            await append_event(
                self.session,
                game_id=self.game.id,
                type=EventType.TURN_STARTED,
                payload={
                    "player_id": str(self.player.id),
                    "colour": self.colour.value,
                    "ply": ply,
                    "model": self.model,
                },
            )
        await self.live.send(
            self.game.id,
            live_turn(self.player.id, colour=self.colour.value, ply=ply, model=self.model),
        )

        offered_by = await open_draw_offer(self.session, game=self.game)
        draw_offered = offered_by is not None and offered_by != self.player.id

        try:
            await self._play(turn, result, draw_offered=draw_offered)
        except LlmError as error:
            # The provider's failure, not the model's (AGENT-09, invariant 11): the turn fails, the
            # position is untouched and the worker decides whether to pause, retry or abandon.
            result.status = TurnStatus.FAILED
            result.error = str(error)
            result.rate_limit = error.rate_limit
            result.request_rejected = error.request_rejected
        except MalformedDecisionError as error:
            # An answer that is not an answer to the question. The endpoint's fault in the only
            # sense that matters here — nothing the model *chose* — so it is a failed turn, never a
            # forfeit, and a later attempt may well be answered properly.
            result.status = TurnStatus.FAILED
            result.error = f"{self.model} returned an unusable decision: {error}"

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        await self._finalise(turn, result)
        return result

    async def _play(self, turn: Turn, result: TurnResult, *, draw_offered: bool) -> None:
        # A copy, history included: working out the facts plays each move and takes it back, and
        # the referee's board is not ours to push to, however briefly.
        board = self.referee.board.raw.copy()
        request = build_request(
            board,
            draw_offered=draw_offered,
            draw_claimable=self._claimable(),
            may_offer_draw=await self._may_offer(),
        )

        # **Asked even with one legal move.** The move is then settled, but resigning, claiming and
        # offering are not, and a seat skipped on a forced move would be one never asked whether
        # to resign in the positions most likely to deserve it.
        decision = await self.gateway.decide(
            request.body(model=self.model), session_id=session_for_game(self.game.id)
        )
        # Recorded before it is read: a response that turns out to be unusable was still a call we
        # made and paid for, and the verbatim row is how anyone finds out what came back.
        await self._record_call(turn, decision)
        result.llm_calls = 1
        result.prompt_tokens = decision.usage.prompt
        result.completion_tokens = decision.usage.completion
        result.cost_usd = decision.cost_usd

        # Every answer is read — and so validated — before anything is acted on, so a malformed
        # one fails the turn cleanly rather than half-way through ending the game.
        chosen, probabilities = decision.choice(MOVE_QUESTION, set(request.moves))
        answers = {
            key: decision.noul(key)
            for key in (
                CLAIM_DRAW_QUESTION,
                ACCEPT_DRAW_QUESTION,
                RESIGN_QUESTION,
                OFFER_DRAW_QUESTION,
            )
            if key in request.questions
        }

        # **One action per turn, in a fixed order, the way a player at a board would take them.**
        # A draw on the table — claimable by right, or offered — is taken before resigning, since
        # a seat that thinks it is lost prefers the half point. Resigning comes before the move,
        # because a resigned player does not move. The offer rides with the move, as it does over
        # a board and as `offer_draw` does for a chat seat (ADR-0040).
        if answers.get(CLAIM_DRAW_QUESTION, 0.0) >= CLAIM_DRAW_AT:
            action = "claim_draw"
        elif answers.get(ACCEPT_DRAW_QUESTION, 0.0) >= ACCEPT_DRAW_AT:
            action = "accept_draw"
        elif answers.get(RESIGN_QUESTION, 0.0) >= RESIGN_AT:
            action = "resign"
        else:
            action = "move"
        offers = action == "move" and answers.get(OFFER_DRAW_QUESTION, 0.0) >= OFFER_DRAW_AT

        await self._record_decision(
            decision,
            request,
            chosen=chosen,
            probabilities=probabilities,
            answers=answers,
            action=action,
            offers=offers,
        )

        result.status = TurnStatus.COMPLETED
        if action == "claim_draw":
            result.outcome = self.referee.claim_draw()
            return
        if action == "accept_draw":
            result.outcome = self.referee.agree_draw()
            return
        if action == "resign":
            result.outcome = self.referee.resign(self.colour)
            return

        await self._move(turn, result, request.moves[chosen])
        if offers and not self.referee.is_over:
            await self._record_draw_offer()

    async def _may_offer(self) -> bool:
        """Whether this seat may offer a draw now: not while its last offer stands declined.

        **Once declined, not again until the position has changed** — a capture or a pawn move
        since the offer. The etiquette over a board, and FIDE's own (11.5: repeated offers can be
        penalised as a distraction). A seat that stood worse in the first long real game offered on
        thirty of its thirty-six turns; between two models that is only noise, but against a
        person it is an accept button beside nearly every move (ADR-0049).

        Read from the log and the board rather than stored: the last `draw_offered` this seat made
        says when, and the fifty-move counter says whether a capture or pawn move has reset it
        since. If the counter has run the whole way from the offer to now, nothing irreversible
        has happened, and the answer the opponent gave by moving on still stands.
        """
        offer = await self.session.scalar(
            sa.select(GameEvent)
            .where(
                GameEvent.game_id == self.game.id,
                GameEvent.type == EventType.DRAW_OFFERED,
                GameEvent.payload["player_id"].astext == str(self.player.id),
            )
            .order_by(GameEvent.seq.desc())
            .limit(1)
        )
        if offer is None:
            return True
        since = self.referee.ply - int(offer.payload.get("ply", 0))
        return self.referee.board.raw.halfmove_clock < since

    def _claimable(self) -> str | None:
        """Which claimable draw is open to this seat now, by the referee's own tests.

        The same two checks `Referee.claim_draw` makes, asked of the same board, so the seat is
        never asked about a claim the referee would then refuse.
        """
        board = self.referee.board
        if board.is_threefold_repetition():
            return THREEFOLD
        if board.is_fifty_move_rule():
            return FIFTY_MOVES
        return None

    async def _move(self, turn: Turn, result: TurnResult, uci: str) -> None:
        # The referee is still the authority (invariant 1): the move is one we offered because it
        # was legal, and it is played through the same validation every other move is.
        move = self.referee.play(uci)
        result.move = move
        result.outcome = move.outcome
        result.status = TurnStatus.COMPLETED
        await self._record_move(turn, move)

    # ------------------------------------------------------------------ persistence

    async def _record_call(self, turn: Turn, decision: Decision) -> None:
        self.session.add(
            LlmCall(
                game_id=self.game.id,
                turn_id=turn.id,
                sequence=1,
                model_slug=self.model,
                provider=decision.provider,
                request=decision.request,
                response=decision.response,
                reasoning_text=None,
                prompt_tokens=decision.usage.prompt,
                completion_tokens=decision.usage.completion,
                reasoning_tokens=0,
                cached_tokens=0,
                cost_usd=decision.cost_usd,
                latency_ms=decision.latency_ms,
                finish_reason=None,
            )
        )
        await self.session.flush()

    async def _record_decision(
        self,
        decision: Decision,
        request: DecisionRequest,
        *,
        chosen: str,
        probabilities: dict[str, float],
        answers: dict[str, float],
        action: str,
        offers: bool,
    ) -> None:
        """The answer, as an event the pages can draw and a frame a spectator sees at once."""
        payload: dict[str, Any] = {
            "player_id": str(self.player.id),
            "colour": self.colour.value,
            "ply": self.referee.ply + 1,
            "model": decision.model,
            "duration_ms": decision.latency_ms,
            # What the seat did — public the moment it happens, so never withheld.
            "action": action,
            "choice": chosen,
            "offers_draw": offers,
            "options": len(request.moves),
            # Every legal move, most likely first — the whole distribution is the model's answer,
            # and the chosen move alone would hide how sure it was and what it nearly played.
            "probabilities": [
                [san, probability]
                for san, probability in sorted(
                    probabilities.items(), key=lambda kv: (-kv[1], kv[0])
                )
            ],
            "confidence": decision.confidence(MOVE_QUESTION),
            # The yes-probability of every other question it was asked this turn.
            "answers": answers,
        }
        await append_event(
            self.session, game_id=self.game.id, type=EventType.DECIDED, payload=payload
        )
        await self.live.send(
            self.game.id,
            live_block(
                self.player.id,
                "decision",
                **{k: v for k, v in payload.items() if k != "player_id"},
            ),
        )

    async def _record_draw_offer(self) -> None:
        """The offer, as the one event every reader of offers already reads (ADR-0040).

        Shaped exactly as `TurnRunner._record_draw_offer` writes it, so `open_draw_offer` finds it,
        a chat opponent's next turn prompt carries it, and a person sees the accept button.
        """
        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.DRAW_OFFERED,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "ply": self.referee.ply,
            },
        )

    async def _record_move(self, turn: Turn, move: MoveOutcome) -> None:
        # Shaped exactly as `TurnRunner._record_move` writes it, because the board, the replay and
        # the archive all read this one event and none of them should care who made the move.
        await record_ply(
            self.session, game_id=self.game.id, colour=self.colour, move=move, turn_id=turn.id
        )
        turn.ply_number = move.ply
        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.MOVE_MADE,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "ply": move.ply,
                "san": move.move.san,
                "uci": move.move.uci,
                "fen": move.fen_after,
                "check": move.move.is_check,
            },
        )

    async def _finalise(self, turn: Turn, result: TurnResult) -> None:
        if result.status is TurnStatus.RUNNING:  # pragma: no cover - every path sets it
            result.status = TurnStatus.FAILED

        turn.status = result.status
        turn.llm_call_count = result.llm_calls
        turn.tool_call_count = 0
        turn.illegal_attempts = 0
        turn.prompt_tokens = result.prompt_tokens
        turn.completion_tokens = result.completion_tokens
        turn.reasoning_tokens = 0
        turn.cached_tokens = 0
        turn.cost_usd = result.cost_usd
        turn.latency_ms = result.latency_ms
        turn.error = result.error
        turn.ended_at = sa.func.now()

        await self.session.execute(
            sa.update(Player)
            .where(Player.id == self.player.id)
            .values(
                prompt_tokens=Player.prompt_tokens + result.prompt_tokens,
                completion_tokens=Player.completion_tokens + result.completion_tokens,
                total_cost_usd=Player.total_cost_usd + result.cost_usd,
            )
        )
        await self.session.execute(
            sa.update(Game)
            .where(Game.id == self.game.id)
            .values(
                total_cost_usd=Game.total_cost_usd + result.cost_usd,
                total_tokens=Game.total_tokens + result.prompt_tokens + result.completion_tokens,
            )
        )
        await self.session.flush()


__all__ = ["ACCEPT_DRAW_AT", "DecisionTurnRunner"]
