"""Which named opening a game reached, from Lichess's opening table (`openings.tsv`).

**The table** is [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings) at
commit `a6189a30dc273ccb21fc2536a9a2fefd5592a67a` (2026-10-06): `a.tsv` to `e.tsv` joined under one
header, otherwise unchanged. CC0, so it carries no attribution requirement; it is credited anyway.
To update it, join the five files at a newer commit the same way and change the commit here.

**It sits beside this module, not in a `data/` directory**, because `.gitignore` ignores every
`data/`: the file would have stayed on the machine that added it, and the image built from the
repository would have had no table — every finished game page failing on its first read.

**Matched by position, not by move order.** A game that reaches the Queen's Gambit Declined through
1. c4 e6 2. Nc3 d5 3. d4 is in the Queen's Gambit Declined, however it got there. Each row of the
table is played out once and keyed by its EPD, which leaves out the move clocks, so the same
position matches whichever line it came from.

**The deepest named position wins.** A game is walked ply by ply and every position the table
names is remembered, so a game that leaves the book on move 9 is named for where it was on move 8.
A game that never reaches a named position is not given one: no answer is better than a guess.

Only from the standard starting position. A game set up from another FEN cannot be in an opening.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

import chess

#: The table's deepest line is 36 plies. A game is never walked further than this, so naming a
#: 300-ply game costs what naming a 40-ply one does.
MAX_BOOK_PLIES = 40

_MOVE_NUMBER = re.compile(r"^\d+\.(\.\.)?$")


@dataclass(frozen=True, slots=True)
class Opening:
    eco: str
    name: str
    #: The ply at which the game reached this position: how long it stayed in the book.
    ply: int


@functools.cache
def _book() -> dict[str, tuple[str, str]]:
    """Every named position, by EPD. Built once per process: ~3,900 lines, under a second."""
    text = resources.files("chessmark.game").joinpath("openings.tsv").read_text("utf-8")
    book: dict[str, tuple[str, str, int]] = {}
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        eco, name, pgn = line.split("\t")
        board = chess.Board()
        sans = [token for token in pgn.split() if not _MOVE_NUMBER.match(token)]
        for san in sans:
            board.push_san(san)
        key = board.epd()
        # Two lines can name one position — a transposition. The longer line is the more specific
        # name for it, and keeping the first seen would make the answer depend on the file order.
        if key not in book or len(sans) > book[key][2]:
            book[key] = (eco, name, len(sans))
    return {key: (eco, name) for key, (eco, name, _) in book.items()}


def identify(sans: Sequence[str], start_fen: str = chess.STARTING_FEN) -> Opening | None:
    """The deepest named opening this game reached, or None if it reached none."""
    if start_fen != chess.STARTING_FEN:
        return None
    book = _book()
    board = chess.Board()
    found: Opening | None = None
    for ply, san in enumerate(sans[:MAX_BOOK_PLIES], start=1):
        try:
            board.push_san(san)
        except ValueError:
            # The moves come from the record, which the referee validated. A move that will not
            # replay means the record is not what this was given, and naming it would be a guess.
            return found
        named = book.get(board.epd())
        if named is not None:
            found = Opening(eco=named[0], name=named[1], ply=ply)
    return found
