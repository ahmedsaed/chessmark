#!/usr/bin/env python3
"""Reopen a game the harness stopped.

    make resume GAME=<id> USD=1.50

Only endings **Chessmark** imposed can be reopened — a budget, its own ply cap, a provider it
could not reach. A checkmate is final, and so is a forfeit: both are findings about a player, and
un-ending one would let a bad result be replayed until it improved. The refusal is the point of
this script existing rather than a hand-edited `UPDATE`.

Appends a `game_resumed` event, so the reason a finished game started moving again is in the same
log everything else reads (ADR-0008, invariant 7) — **and tells the players**, which is the part
that was missing. See `_tell_the_players`.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from chessmark.agents.prompts import PROMPT_VERSION  # noqa: E402
from chessmark.agents.transcript import append_message  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.enums import EventType, GameStatus  # noqa: E402
from chessmark.db.models import Game, GameEvent, Player, TournamentGame  # noqa: E402
from chessmark.db.repositories import append_event, get_game, rebuild_referee  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import (  # noqa: E402
    FORFEIT_TERMINATIONS,
    RESUMABLE_TERMINATIONS,
    GameResult,
    Termination,
)
from chessmark.orchestration import AdvanceTurn, TurnQueue  # noqa: E402

#: The two draws that were applied without a claim before ADR-0020.
_UNCLAIMED = frozenset({Termination.THREEFOLD_REPETITION, Termination.FIFTY_MOVE_RULE})


async def _tell_the_players(
    session: Any, game: Any, *, previous: Any, ply: int
) -> int:
    """Append a message to each seat saying the game is live again. Returns how many were told.

    **A model that is told the game is over and then asked to move is right to refuse.** Game
    `8692cba1` reached the 300-ply cap, and its own `make_move` result said so —
    `{"game_over": true, "result": "1/2-1/2", "termination": "ply_cap"}`. It was reopened here with
    a raised cap, which clears the ending from the *game record* and left the *transcript* saying
    the game had finished. The next turn asked Black to move at ply 302; Black answered four times
    that the game had already ended "according to the terminal state reported by the system", and
    the harness forfeited it for not calling a tool. The result was a rated 1-0 against a model
    that was doing exactly what its context said to do — a harness bound recorded as a finding
    about a player, which is the one thing invariant 11 exists to prevent.

    **An append, not an edit.** The earlier "game over" stays exactly where it is: the transcript
    is rows, the prefix must stay byte-identical for prompt caching, and rewriting history is what
    invariant 2 forbids. This adds the correction at the end, which is how a player would be told
    anything else.

    Both seats, not only the one to move. The ending was announced in whichever transcript happened
    to call `make_move` last, and the opponent's next prompt is an ordinary "it is your move" that
    would read as normal — but a model that saw the result in its own tool output and a model that
    did not should not be given different accounts of the same game.
    """
    if previous is None:
        return 0

    detail = game.termination_detail or ""
    notice = (
        f"The game was recorded as finished — {previous}"
        + (f": {detail}" if detail else "")
        + " — and that ending has been set aside. Disregard any earlier message in this "
        f"conversation saying the game is over: it is live again from ply {ply}, the ply cap is "
        f"now {game.max_plies}, and it is played on from the position on the board. "
        "Call `make_move` when it is your move."
    )

    players = list(await session.scalars(sa.select(Player).where(Player.game_id == game.id)))
    for player in players:
        await append_message(
            session,
            player_id=player.id,
            game_id=game.id,
            role="user",
            content=notice,
        )
    return len(players)


async def _unsettle_pairing(session: Any, game: Any) -> str | None:
    """Drop the verdict this game's tournament pairing still holds. Returns what it was, or None.

    **Both halves of it, and that is the whole point.** A pairing carries either an
    `abandoned_reason` or a `white_score`, and a resumed game must shed whichever it has: the
    verdict being reopened is exactly the one written there.

    Clearing only the abandonment was the first version of this, and it left a worse bug than it
    fixed. `white_score` means "this pairing is decided" — the column's own comment says *null
    while the game is unplayed or in flight* — so four resumed games ran for up to 89 plies while
    the schedule showed them as **played**, with the score of the forfeit that had just been
    overturned, and the event reported `live: 0` with four boards moving. The homepage, reading the
    games directly, disagreed with the tournament page, which is how it was noticed.

    Extracted from `main` so it can be tested. Inline, it was the one correction a resume makes
    that no test could reach — `_clear_stale_forfeits` beside it has three, and this had none while
    being the half that had already regressed once.
    """
    pairing = await session.scalar(
        sa.select(TournamentGame).where(TournamentGame.game_id == game.id)
    )
    if pairing is None:
        return None
    if pairing.abandoned_reason is None and pairing.white_score is None:
        return None

    was = "abandoned" if pairing.abandoned_reason else f"scored {pairing.white_score}"
    pairing.abandoned_reason = None
    pairing.white_score = None
    # `ended_at` is the third column and is as stale as the other two. A pairing with no score and
    # an end time reads as finished-but-unscored, which is what an abandonment looks like.
    pairing.ended_at = None
    await session.flush()
    return was


async def _clear_stale_forfeits(session: Any, game: Any, previous: Any, *, corrected: bool) -> int:
    """Drop the seat's `forfeited` flag when the ending that wrote it is being reopened.

    **The same shape as un-settling the pairing, and it was missed for the same reason.** The flag
    is a *verdict*, it was written by the ending this reopens, and unlike the pairing's score it is
    published: `bench.service` counts it into the leaderboard's forfeits column, over exactly the
    games a resume makes ratable again.

    It gets set for a harness stop because `BUDGET_EXCEEDED` travels as `TurnStatus.FORFEITED` —
    the turn does end the game — while `ratable.HARNESS_TERMINATIONS` says just as plainly that it
    is not a finding. Two free-pool games were budget-stopped, reopened, and played on to a real
    checkmate and a real threefold draw; both stayed ratable and both models carried a forfeit
    nothing in their play had earned (ADR-0024). The turn loop no longer writes one; this clears
    the ones it already wrote.

    Refuses when the ending being reopened *was* a forfeit, **unless a gate has already found that
    forfeit to be ours**. Clearing the flag on a genuine forfeit would erase a finding rather than a
    mistake, so the general rule stands — but `--overwritten-verdict` passes only when the *first*
    ending was a harness stop, which is evidence the operator cannot fabricate and the same evidence
    that allowed the reopen at all.

    Without `corrected`, repairing `8692cba1` would have reopened the game and left Liquid carrying
    a forfeit for it: the flag is published, `bench.service` counts it into the leaderboard's
    forfeits column, and the game it was written for no longer ends that way. That is the exact
    failure this function exists for, declined on a technicality.
    """
    if previous in FORFEIT_TERMINATIONS and not corrected:
        return 0
    cleared = await session.execute(
        sa.update(Player)
        .where(Player.game_id == game.id, Player.forfeited.is_(True))
        .values(forfeited=False)
    )
    return int(cleared.rowcount or 0)


async def _verdict_was_overwritten(session: Any, game: Any) -> tuple[bool, str]:
    """Whether this game's stored ending replaced an earlier harness stop.

    A game should append one `game_ended` row. Before ADR-0022, two workers could play the same ply
    at once and the loser wrote its verdict over the winner's — one game ended **seven** times.
    Where the first ending was a harness stop and a later one is a forfeit, the rated verdict was
    chosen by scheduling.

    **A race is not the only way to get two endings**, which is why this checks the record rather
    than the cause. `8692cba1` ended at its ply cap, was reopened here with a raised one, and then
    forfeited a model that had been told by our own tool that the game was over. The shape in the
    log is identical — a harness stop, then a finding — and so is the correction.

    Reopens on the **first** ending, which is the one the race overwrote. Not the most favourable —
    the first, whatever it says — because a script that picks among real endings is a script that
    can improve a result by running it again.
    """
    endings = list(
        await session.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == game.id, GameEvent.type == EventType.GAME_ENDED)
            .order_by(GameEvent.seq)
        )
    )
    if len(endings) < 2:
        return False, "this game ended once; there is no overwritten verdict to restore"

    first = str(endings[0].payload.get("termination") or "")
    if first == str(game.termination):
        return False, f"the stored verdict is already the first one written ({first})"
    if first not in {str(t) for t in RESUMABLE_TERMINATIONS}:
        return False, f"the first ending was {first}, which is a finding about a player"
    return True, f"{len(endings)} endings recorded; the first was {first}, overwritten by a race"


def _unclaimed_draw_is_reopenable(game: Any) -> tuple[bool, str]:
    """Whether this specific draw was one the players never had a say in.

    Deliberately narrow, because the general rule must keep holding: a chess result is final, and a
    script that can reopen any draw is a script that can replay a bad result until it improves.

    Two conditions, both necessary. The termination has to be one of the claimable pair — a
    checkmate or a fivefold backstop is nobody's fault but the player's. And the game has to
    predate the prompt that disclosed the rule: from v2 on, a model is told that repetition is
    claimable and given `claim_draw`, so a draw it walked into is a finding about it.
    """
    if game.termination not in _UNCLAIMED:
        return False, f"{game.termination} was never an unclaimed draw"

    if game.prompt_version == PROMPT_VERSION:
        return False, (
            f"this game ran under prompt {game.prompt_version}, which states the rule and offers "
            "`claim_draw` — walking into the draw was its own doing"
        )

    return True, (
        f"played under prompt {game.prompt_version}, which never mentioned the rule; "
        f"ratings are for {PROMPT_VERSION}"
    )


#: The shortest prefix `resolve_game_id` will accept.
#:
#: Eight is what the site shows — the games list, the replay header and every log line print
#: `game.id[:8]` — so it is the length an operator already has in front of them, and it is the
#: length these commands get typed with. Shorter is refused rather than resolved: a two-character
#: prefix is *usually* unique across a few hundred games and stops being so exactly when the event
#: grows, and a resume command that silently starts matching a different game as the pool fills is
#: not a convenience.
MIN_PREFIX = 8


async def resolve_game_id(session: AsyncSession, given: str) -> uuid.UUID:
    """A full UUID, or an unambiguous prefix of one.

    Every id a person reads off this project is already abbreviated — `game 9b372624` in the replay
    header, `abandoning 9b372624-…` in the worker log — and then the command to act on it demanded
    all thirty-six characters, which meant copying them out of a URL or a database.

    **Ambiguity is refused, never guessed.** A prefix matching two games prints both and exits: the
    one thing worse than typing a full id is reopening the wrong game, and a resume is a state
    change on a record the leaderboard reads.
    """
    try:
        return uuid.UUID(given)
    except ValueError:
        pass

    prefix = given.strip().lower()
    if len(prefix) < MIN_PREFIX:
        msg = f"{given!r} is too short — give at least {MIN_PREFIX} characters, or the full id"
        raise SystemExit(msg)

    # Cast to text and match on the front. `id::text` renders the canonical hyphenated form, which
    # is what a person copies, so a prefix spanning a hyphen works without special-casing it.
    matches: list[uuid.UUID] = list(
        await session.scalars(
            sa.select(Game.id).where(sa.cast(Game.id, sa.Text).like(f"{prefix}%")).limit(10)
        )
    )
    if not matches:
        msg = f"no game whose id starts with {prefix!r}"
        raise SystemExit(msg)
    if len(matches) > 1:
        listed = "\n  ".join(str(m) for m in matches)
        msg = f"{prefix!r} matches {len(matches)} games — say which:\n  {listed}"
        raise SystemExit(msg)
    return matches[0]


def _paused_refusal(game: Any) -> str | None:
    """Why a paused game cannot be reopened, or `None` when it is not paused.

    **A paused game has not ended, so there is nothing to reopen.** Without this it fell through to
    the resumable check and was refused for having *"ended by None"* — true, and unhelpful: it
    reads as a broken record rather than as a game still alive and waiting on a provider. An
    operator seeing that goes debugging instead of waiting.

    Says what it is waiting on and when it comes back, because those are the two things that decide
    whether to do anything at all.
    """
    if game.status is not GameStatus.PAUSED:
        return None

    waiting = "its wait is over and the reconciler will pick it up on the next tick"
    resume_after = game.resume_after
    if resume_after is not None:
        if resume_after.tzinfo is None:
            resume_after = resume_after.replace(tzinfo=dt.UTC)
        left = (resume_after - dt.datetime.now(dt.UTC)).total_seconds() / 60
        if left > 0:
            waiting = f"it resumes on its own in about {left:.0f} minutes"

    reason = game.pause_reason or "waiting on its provider"
    return f"game is paused, not ended — {reason}. Nothing to reopen: {waiting}."


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "game_id",
        help=(
            "the game's id, or an unambiguous prefix of at least "
            f"{MIN_PREFIX} characters — the form the site and the logs print"
        ),
    )
    parser.add_argument(
        "--max-usd",
        type=Decimal,
        default=None,
        help="New per-game budget. Must exceed what has already been spent, or the game stops again immediately.",
    )
    parser.add_argument("--max-plies", type=int, default=None, help="New ply cap.")
    parser.add_argument(
        "--overwritten-verdict",
        action="store_true",
        help=(
            "reopen a game whose stored ending replaced an earlier harness stop, written when two "
            "workers played the same ply at once (ADR-0022)."
        ),
    )
    parser.add_argument(
        "--unclaimed-draw",
        action="store_true",
        help=(
            "reopen a game drawn by threefold repetition or the fifty-move rule that nobody "
            "claimed. Only valid for a game played under a prompt that never disclosed the rule."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    sessionmaker = get_sessionmaker()
    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    await queue.ensure_group()

    try:
        async with sessionmaker() as session:
            game = await get_game(session, await resolve_game_id(session, args.game_id))

            if game.status is GameStatus.RUNNING:
                print("game is already running", file=sys.stderr)
                return 1

            paused = _paused_refusal(game)
            if paused is not None:
                print(paused, file=sys.stderr)
                return 1

            resumable = game.termination in RESUMABLE_TERMINATIONS
            if not resumable and args.unclaimed_draw:
                resumable, refusal = _unclaimed_draw_is_reopenable(game)
                if not resumable:
                    print(refusal, file=sys.stderr)
                    return 2
                print(f"reopening an unclaimed {game.termination} draw ({refusal})")

            # This reopens a *forfeit*, which the general rule refuses, so it is gated on the
            # record rather than on the flag: the operator says which correction they mean and the
            # event log decides whether it applies.
            #
            # `--harness-ceiling` stood here too, reopening a `truncated` forfeit where the stored
            # calls showed our own `max_tokens` had cut the response. It is gone because the
            # question it asked no longer has two answers: a truncation is a harness stop either
            # way (ADR-0024), so `TRUNCATED` is in `RESUMABLE_TERMINATIONS` and a plain resume
            # reopens it. A flag that can never fire is worse than no flag.
            if not resumable and args.overwritten_verdict:
                resumable, why = await _verdict_was_overwritten(session, game)
                if not resumable:
                    print(why, file=sys.stderr)
                    return 2
                print(f"reopening a verdict a later ending overwrote ({why})")

            if not resumable:
                print(
                    f"refusing to reopen a game that ended by {game.termination}. "
                    "Only a budget, a ply cap, or an unreachable provider may be reopened — "
                    "a chess result and a forfeit are findings about a player."
                    + (
                        " An automatic threefold or fifty-move draw from before ADR-0020 can be "
                        "reopened with --unclaimed-draw."
                        if game.termination in _UNCLAIMED
                        else ""
                    )
                    + " If a race overwrote an earlier harness stop, --overwritten-verdict does.",
                    file=sys.stderr,
                )
                return 2

            if args.max_usd is not None:
                if args.max_usd <= game.total_cost_usd:
                    print(
                        f"budget ${args.max_usd} is at or below the ${game.total_cost_usd:.4f} "
                        "already spent — the game would stop again on its first turn",
                        file=sys.stderr,
                    )
                    return 2
                game.max_usd = args.max_usd

            if args.max_plies is not None:
                game.max_plies = args.max_plies

            referee = await rebuild_referee(session, game)

            # Clearing the outcome is what actually reopens it: `rebuild_referee` re-applies a
            # stored termination on every turn, so a game left FINISHED would conclude again
            # before playing a move.
            previous = game.termination
            game.status = GameStatus.RUNNING

            was = await _unsettle_pairing(session, game)
            if was is not None:
                print(f"re-opened its tournament pairing (was {was}) so it can settle again")
            game.result = GameResult.ONGOING
            game.termination = None
            game.termination_detail = None
            game.ended_at = None

            # `corrected` is the gate's own finding: `--overwritten-verdict` passes only when the
            # first ending was a harness stop, so the forfeit it reopens is one we produced.
            cleared = await _clear_stale_forfeits(
                session, game, previous, corrected=bool(args.overwritten_verdict)
            )
            if cleared:
                print(
                    f"cleared a stale forfeit on {cleared} seat(s) "
                    f"(written by the {previous} this reopens)"
                )

            told = await _tell_the_players(session, game, previous=previous, ply=referee.ply)
            if told:
                print(f"told {told} seat(s) the game is live again")

            await append_event(
                session,
                game_id=game.id,
                type=EventType.GAME_RESUMED,
                payload={
                    "previous_termination": str(previous),
                    "ply": referee.ply,
                    "max_usd": str(game.max_usd) if game.max_usd else None,
                    "max_plies": game.max_plies,
                    "spent_usd": str(game.total_cost_usd),
                },
            )
            await session.commit()
            job = AdvanceTurn(game_id=game.id, expected_ply=referee.ply)

        await queue.enqueue(job)
        print(
            f"resumed {game.id} from ply {referee.ply} "
            f"(was {previous}, spent ${game.total_cost_usd:.4f}, budget now ${game.max_usd})"
        )
        print("a worker must be running: make worker")
        return 0
    finally:
        await redis.aclose()
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
