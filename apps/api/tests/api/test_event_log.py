"""`GET /games/{id}/events` — the paged read of the log (ADR-0008).

Replay rebuilds the board by walking moves from ply 0, so **a row this endpoint fails to deliver
is a wrong position on the page, not merely a short list.** That makes "did the caller get all of
it, and can it tell?" the property worth pinning, and it is the one that was missing.

What was here before: a truncated page had the game's *terminal* events appended to it, so a
reader who asked once still saw how the game ended. Well-intentioned, but it produced a result
that looked continuous and was not — the middle was gone with nothing saying so — and it broke
the cursor, because the last `seq` in the page was no longer the boundary of what had been read.
Following it re-fetched the truncated middle or skipped it, depending on which row you followed.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType
from chessmark.db.repositories import append_event
from chessmark.orchestration.queue import TurnQueue
from tests.support import Fixture, seat_match

pytestmark = pytest.mark.integration


async def a_long_log(db: AsyncSession, queue: TurnQueue, rows: int) -> Fixture:
    """A game with more events than any one page will hold, ending in `game_ended`.

    Written directly rather than played, because the property is about row counts and playing a
    300-ply game to produce them would make this test minutes long for no extra coverage.
    """
    game = await seat_match(db, queue)
    for index in range(rows):
        await append_event(
            db,
            game_id=game.game.id,
            type=EventType.MOVE_MADE,
            payload={"ply": index, "san": "e4"},
        )
    await append_event(
        db, game_id=game.game.id, type=EventType.GAME_ENDED, payload={"result": "1-0"}
    )
    await db.commit()
    return game


async def follow_the_cursor(
    client: AsyncClient, game_id: object, *, page: int
) -> list[dict[str, object]]:
    """Read the whole log the way the web client does, and assert nothing is skipped or repeated.

    The assertions live in the helper on purpose: every test below reads through it, so the
    no-gap, no-duplicate property is checked on each of them rather than in one case of its own.
    """
    out: list[dict[str, object]] = []
    after = 0
    for _ in range(50):
        batch = (await client.get(f"/games/{game_id}/events?after_seq={after}&limit={page}")).json()
        assert all(row["seq"] > after for row in batch), "a page re-sent a row already delivered"
        out += batch
        if len(batch) < page:
            break
        after = batch[-1]["seq"]
    return out


async def test_a_full_page_is_followed_by_the_rest(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    game = await a_long_log(db, queue, rows=120)

    every = await follow_the_cursor(client, game.game.id, page=25)
    seqs = [row["seq"] for row in every]

    assert seqs == sorted(seqs), "the log came back out of order"
    assert len(seqs) == len(set(seqs)), "a row arrived twice"
    assert every[-1]["type"] == "game_ended", (
        "following the cursor to a short page must reach the end of the game"
    )


async def test_one_page_of_a_long_log_says_there_is_more(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """A caller that reads one page and stops sees a *prefix*, and a full page is how it knows.

    This is what the terminal-append broke: the page came back longer than `limit`, holding the
    front of the game and its ending with the middle silently absent, and a caller inspecting
    `len(page) == limit` to decide whether to continue got `False` on a game that had barely
    started.
    """
    game = await a_long_log(db, queue, rows=120)

    page = (await client.get(f"/games/{game.game.id}/events?limit=25")).json()

    assert len(page) == 25, f"a page must hold exactly its limit, got {len(page)}"
    assert page[0]["seq"] < page[-1]["seq"]
    assert all(row["type"] != "game_ended" for row in page), (
        "the ending was spliced onto a truncated page again — the middle of the log is missing "
        "and nothing in the result says so"
    )


async def test_the_last_page_is_short_even_when_the_log_divides_evenly(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """The cursor contract's one awkward case: when the rows are an exact multiple of the page
    size, the final request returns nothing, and that empty page is the terminator. A client that
    treated an empty page as an error would stop one page early on exactly these games."""
    game = await a_long_log(db, queue, rows=20)
    total = len((await client.get(f"/games/{game.game.id}/events?limit=5000")).json())

    last = (await client.get(f"/games/{game.game.id}/events?after_seq={total}&limit=10")).json()

    assert last == []


async def test_reading_the_log_costs_a_fixed_number_of_queries(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """One page is one read, whatever is in it (CLAUDE.md, *a new read endpoint is measured*).

    The withholding check adds a constant — it asks once whether this game has a person in it —
    and that is the whole cost. What this forbids is a per-row read creeping in behind
    `EventOut.from_model`.
    """
    game = await a_long_log(db, queue, rows=200)

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        await client.get(f"/games/{game.game.id}/events?limit=10")
        ten = len(statements)
        statements.clear()
        await client.get(f"/games/{game.game.id}/events?limit=200")
        two_hundred = len(statements)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert two_hundred == ten, (
        f"a 10-row page took {ten} queries and a 200-row page took {two_hundred} — "
        "the log is being read per row"
    )
