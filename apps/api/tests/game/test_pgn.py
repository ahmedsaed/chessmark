"""PGN export and full-game replay (GAME-05).

The replay test is the strongest single check in this phase: a real, published game played
ply-by-ply through our own referee must land on exactly the known final position. If any rule is
subtly wrong, this fails.
"""

from __future__ import annotations

import pytest

from chessmark.game import (
    Colour,
    GameResult,
    PgnMetadata,
    Referee,
    Termination,
    final_position_from_pgn,
    to_pgn,
)

# Morphy vs. Duke Karl / Count Isouard, Paris 1858 — "the Opera Game".
OPERA_GAME = [
    "e4",
    "e5",
    "Nf3",
    "d6",
    "d4",
    "Bg4",
    "dxe5",
    "Bxf3",
    "Qxf3",
    "dxe5",
    "Bc4",
    "Nf6",
    "Qb3",
    "Qe7",
    "Nc3",
    "c6",
    "Bg5",
    "b5",
    "Nxb5",
    "cxb5",
    "Bxb5+",
    "Nbd7",
    "O-O-O",
    "Rd8",
    "Rxd7",
    "Rxd7",
    "Rd1",
    "Qe6",
    "Bxd7+",
    "Nxd7",
    "Qb8+",
    "Nxb8",
    "Rd8#",
]
OPERA_GAME_FINAL_FEN = "1n1Rkb1r/p4ppp/4q3/4p1B1/4P3/8/PPP2PPP/2K5 b k - 1 17"


def _play_opera_game() -> Referee:
    referee = Referee()
    for move in OPERA_GAME:
        referee.play(move)
    return referee


def test_famous_game_replays_to_the_correct_final_position() -> None:
    referee = _play_opera_game()

    assert referee.board.fen == OPERA_GAME_FINAL_FEN
    assert referee.board.ply == len(OPERA_GAME) == 33


def test_famous_game_ends_in_checkmate() -> None:
    referee = _play_opera_game()

    assert referee.is_over
    assert referee.outcome is not None
    assert referee.outcome.termination is Termination.CHECKMATE
    assert referee.outcome.result is GameResult.WHITE_WINS
    assert referee.outcome.winner is Colour.WHITE


def test_famous_game_history_matches_what_was_played() -> None:
    assert _play_opera_game().board.history_san() == OPERA_GAME


def test_every_ply_is_reachable_in_order() -> None:
    """Replay one move at a time, checking the game only ends on the final ply."""
    referee = Referee()
    for index, move in enumerate(OPERA_GAME, start=1):
        assert not referee.is_over, f"game ended early, before ply {index}"
        outcome = referee.play(move)
        assert outcome.ply == index
        assert outcome.move.san == move


# --------------------------------------------------------------------- export


def _metadata() -> PgnMetadata:
    return PgnMetadata(
        white="gpt-oss-20b",
        black="nemotron-3-nano-30b-a3b",
        game_id="8f2a",
        date="2026.08.15",
        ranked=True,
        prompt_version="v1",
        tool_schema_version="v1",
        white_illegal_attempts=0,
        black_illegal_attempts=2,
    )


def test_export_carries_the_seven_tag_roster() -> None:
    pgn = to_pgn(_play_opera_game(), _metadata())

    assert '[Event "Chessmark"]' in pgn
    assert '[Site "chessmark.com"]' in pgn
    assert '[Date "2026.08.15"]' in pgn
    assert '[White "gpt-oss-20b"]' in pgn
    assert '[Black "nemotron-3-nano-30b-a3b"]' in pgn
    assert '[Result "1-0"]' in pgn


def test_export_carries_chessmark_provenance() -> None:
    pgn = to_pgn(_play_opera_game(), _metadata())

    assert '[ChessmarkGameId "8f2a"]' in pgn
    assert '[ChessmarkRanked "true"]' in pgn
    assert '[ChessmarkPromptVersion "v1"]' in pgn
    assert '[ChessmarkToolSchemaVersion "v1"]' in pgn
    assert '[ChessmarkWhiteIllegalAttempts "0"]' in pgn
    assert '[ChessmarkBlackIllegalAttempts "2"]' in pgn
    assert '[Termination "checkmate"]' in pgn


def test_export_contains_the_moves() -> None:
    pgn = to_pgn(_play_opera_game(), _metadata())

    assert "1. e4 e5" in pgn
    assert "17. Rd8#" in pgn
    assert pgn.rstrip().endswith("1-0")


def test_export_round_trips_to_the_same_position() -> None:
    """The strongest export check: our PGN, replayed by a third party, is the same game."""
    referee = _play_opera_game()
    pgn = to_pgn(referee, _metadata())

    assert final_position_from_pgn(pgn) == referee.board.fen == OPERA_GAME_FINAL_FEN


def test_export_from_a_custom_start_records_the_setup() -> None:
    fen = "8/P6k/8/8/8/8/8/K7 w - - 0 1"
    referee = Referee(start_fen=fen)
    referee.play("a8=Q")

    pgn = to_pgn(referee, PgnMetadata(white="white", black="black"))

    assert '[SetUp "1"]' in pgn
    assert f'[FEN "{fen}"]' in pgn
    assert final_position_from_pgn(pgn) == referee.board.fen


def test_unfinished_game_exports_with_an_open_result() -> None:
    referee = Referee()
    referee.play("e4")

    pgn = to_pgn(referee, PgnMetadata(white="white", black="black"))

    assert '[Result "*"]' in pgn
    assert "Termination" not in pgn
    assert '[Date "????.??.??"]' in pgn


def test_extra_tags_are_passed_through() -> None:
    metadata = PgnMetadata(white="w", black="b", extra={"ChessmarkNote": "exhibition"})
    pgn = to_pgn(Referee(), metadata)
    assert '[ChessmarkNote "exhibition"]' in pgn


def test_reading_a_non_game_raises() -> None:
    with pytest.raises(ValueError, match="no game found"):
        final_position_from_pgn("")
