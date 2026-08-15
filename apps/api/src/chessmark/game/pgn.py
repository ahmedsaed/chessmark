"""PGN export (GAME-05).

Standard seven-tag roster plus Chessmark-specific tags, so an exported game carries enough
provenance to be reproduced: which models played, under which prompt and tool-schema versions,
and how many illegal moves each side made.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import chess
import chess.pgn

from chessmark.game.referee import Referee


@dataclass(slots=True)
class PgnMetadata:
    """Provenance for an exported game. Everything here ends up in the PGN header."""

    white: str
    black: str
    game_id: str | None = None
    date: str | None = None
    """UTC date as `YYYY.MM.DD`. The caller supplies it — this module has no clock."""
    event: str = "Chessmark"
    site: str = "chessmark.com"
    round_: str = "-"
    ranked: bool | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    white_illegal_attempts: int | None = None
    black_illegal_attempts: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _custom_tags(metadata: PgnMetadata, referee: Referee) -> dict[str, str]:
    tags: dict[str, str] = {}

    if metadata.game_id:
        tags["ChessmarkGameId"] = metadata.game_id
    if metadata.ranked is not None:
        tags["ChessmarkRanked"] = "true" if metadata.ranked else "false"
    if metadata.prompt_version:
        tags["ChessmarkPromptVersion"] = metadata.prompt_version
    if metadata.tool_schema_version:
        tags["ChessmarkToolSchemaVersion"] = metadata.tool_schema_version
    if metadata.white_illegal_attempts is not None:
        tags["ChessmarkWhiteIllegalAttempts"] = str(metadata.white_illegal_attempts)
    if metadata.black_illegal_attempts is not None:
        tags["ChessmarkBlackIllegalAttempts"] = str(metadata.black_illegal_attempts)

    if referee.outcome:
        tags["Termination"] = str(referee.outcome.termination)
        tags["ChessmarkTerminationDetail"] = referee.outcome.detail

    tags.update(metadata.extra)
    return tags


def to_pgn(referee: Referee, metadata: PgnMetadata) -> str:
    """Render a game as PGN.

    Works mid-game as well as at the end; an unfinished game exports with a `*` result.
    """
    game = chess.pgn.Game()

    game.headers["Event"] = metadata.event
    game.headers["Site"] = metadata.site
    game.headers["Date"] = metadata.date or "????.??.??"
    game.headers["Round"] = metadata.round_
    game.headers["White"] = metadata.white
    game.headers["Black"] = metadata.black
    game.headers["Result"] = str(referee.result)

    root_fen = referee.board.root_fen
    if root_fen != chess.STARTING_FEN:
        game.headers["SetUp"] = "1"
        game.headers["FEN"] = root_fen

    for tag, value in _custom_tags(metadata, referee).items():
        game.headers[tag] = value

    node: chess.pgn.GameNode = game
    for move in referee.board.raw.move_stack:
        node = node.add_variation(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    return game.accept(exporter)


def final_position_from_pgn(pgn_text: str) -> str:
    """Replay a PGN and return the resulting FEN. Used to verify imports and in tests."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        msg = "no game found in PGN text"
        raise ValueError(msg)

    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    return board.fen()
