"""The archive: `GET /games` filtered, searched and paged (UI-12).

Every filter here is asserted against a game it must *exclude* as well as one it must keep. A
filter tested only on a set where everything matches passes when it does nothing at all.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import ModelRegistry, Tournament, TournamentGame
from chessmark.game import Colour, GameResult, Termination
from chessmark.orchestration.match import Seat, create_match

pytestmark = pytest.mark.integration

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


async def _model(db: AsyncSession, slug: str, name: str | None = None) -> None:
    db.add(
        ModelRegistry(
            openrouter_id=slug,
            display_name=name or slug.split("/")[-1],
            provider=slug.split("/")[0],
            prompt_usd_per_token=Decimal("0.0000001"),
            completion_usd_per_token=Decimal("0.0000004"),
        )
    )
    await db.flush()


def _seat(who: str) -> Seat:
    """`human:<name>` is a person; anything else is a registered model's OpenRouter id."""
    if who.startswith("human:"):
        return Seat(display_name=who.removeprefix("human:"), kind=PlayerKind.HUMAN)
    return Seat(display_name=who.split("/")[-1], model=who)


async def _game(
    db: AsyncSession,
    white: str = "acme/alpha",
    black: str = "acme/beta",
    *,
    minute: int = 0,
    status: GameStatus = GameStatus.FINISHED,
    result: GameResult = GameResult.WHITE_WINS,
    termination: Termination | None = Termination.CHECKMATE,
    ranked: bool = False,
    plies: int = 40,
    cost: str = "0.10",
) -> uuid.UUID:
    match = await create_match(db, white=_seat(white), black=_seat(black), is_ranked=ranked)
    game = match.game
    game.status = status
    game.result = result
    game.termination = termination
    game.winner_colour = {
        GameResult.WHITE_WINS: Colour.WHITE,
        GameResult.BLACK_WINS: Colour.BLACK,
    }.get(result)
    game.ply_count = plies
    game.total_cost_usd = Decimal(cost)
    # Set, not left to `now()`: several games committed inside one test can share a timestamp,
    # and the order under test would then be the id's rather than the one being asserted.
    game.created_at = EPOCH + dt.timedelta(minutes=minute)
    await db.commit()
    return game.id


@pytest.fixture
async def models(db: AsyncSession) -> None:
    await _model(db, "acme/alpha", "Alpha One")
    await _model(db, "acme/beta", "Beta Two")
    await _model(db, "other/gamma", "Gamma_Three")
    await db.commit()


async def _ids(client: AsyncClient, **params: object) -> list[str]:
    response = await client.get("/games", params=params)
    assert response.status_code == 200, response.text
    return [game["id"] for game in response.json()]


# ====================================================================== filters


async def test_statuses_are_repeatable(client: AsyncClient, db: AsyncSession, models: None) -> None:
    """How the page hides aborted games: it asks for every other status by name."""
    finished = await _game(db, minute=1)
    running = await _game(db, minute=2, status=GameStatus.RUNNING, result=GameResult.ONGOING)
    await _game(db, minute=3, status=GameStatus.ABORTED, result=GameResult.ONGOING)

    ids = await _ids(client, status=["finished", "running"])

    assert ids == [str(running), str(finished)]


async def test_result_filters_by_outcome(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    white = await _game(db, minute=1, result=GameResult.WHITE_WINS)
    black = await _game(db, minute=2, result=GameResult.BLACK_WINS)
    draw = await _game(db, minute=3, result=GameResult.DRAW, termination=Termination.STALEMATE)

    assert await _ids(client, result="white") == [str(white)]
    assert await _ids(client, result="black") == [str(black)]
    assert await _ids(client, result="draw") == [str(draw)]


async def test_termination_and_ranked(client: AsyncClient, db: AsyncSession, models: None) -> None:
    mate = await _game(db, minute=1, ranked=True)
    forfeit = await _game(db, minute=2, termination=Termination.ILLEGAL_MOVE_FORFEIT)

    assert await _ids(client, termination="illegal_move_forfeit") == [str(forfeit)]
    assert await _ids(client, ranked="true") == [str(mate)]
    assert await _ids(client, ranked="false") == [str(forfeit)]


async def test_kind_separates_people_from_models(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    benchmark = await _game(db, minute=1)
    person = await _game(db, "human:Magnus", "acme/beta", minute=2)

    assert await _ids(client, kind="models") == [str(benchmark)]
    assert await _ids(client, kind="humans") == [str(person)]


async def test_a_matchup_needs_both_models(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    ab = await _game(db, "acme/alpha", "acme/beta", minute=1)
    ba = await _game(db, "acme/beta", "acme/alpha", minute=2)
    await _game(db, "acme/alpha", "other/gamma", minute=3)

    ids = await _ids(client, model="acme/alpha", opponent="acme/beta")

    assert ids == [str(ba), str(ab)], "a pairing is the same pairing with the colours swapped"


async def test_a_model_is_not_its_own_opponent_unless_it_played_itself(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """The case two independent seat checks get wrong: both are true of *any* alpha game."""
    await _game(db, "acme/alpha", "acme/beta", minute=1)
    mirror = await _game(db, "acme/alpha", "acme/alpha", minute=2)

    assert await _ids(client, model="acme/alpha", opponent="acme/alpha") == [str(mirror)]


async def test_a_tournament_lists_its_own_games(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    event = Tournament(name="Spring Open", slug="spring-open", format="round_robin")
    db.add(event)
    await db.flush()
    inside = await _game(db, minute=1)
    await _game(db, minute=2)
    db.add(
        TournamentGame(
            tournament_id=event.id,
            round_number=1,
            white_key="acme/alpha",
            black_key="acme/beta",
            game_id=inside,
        )
    )
    await db.commit()

    assert await _ids(client, tournament="spring-open") == [str(inside)]
    assert await _ids(client, tournament="no-such-event") == []


# ====================================================================== search


async def test_search_matches_either_seats_name(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    person = await _game(db, "acme/alpha", "human:Grandmaster Flash", minute=1)
    await _game(db, minute=2)

    assert await _ids(client, q="flash") == [str(person)], "case-insensitive, and Black's seat"


async def test_search_matches_the_model_id_no_name_contains(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """`other/` appears in no display name; it is only in the registry's id."""
    gamma = await _game(db, "other/gamma", "acme/beta", minute=1)
    await _game(db, minute=2)

    assert await _ids(client, q="other/") == [str(gamma)]


async def test_search_takes_wildcards_literally(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """Unescaped, `_` matches any character and `a_p` would find "Alpha"."""
    gamma = await _game(db, "other/gamma", "acme/beta", minute=1)
    await _game(db, minute=2)

    assert await _ids(client, q="a_T") == [str(gamma)]
    assert await _ids(client, q="%") == []


async def test_search_uses_the_trigram_index(db: AsyncSession) -> None:
    """The index is only worth having if the planner can use it for the shape we issue.

    Sequential scans are switched off for the check, because on a test table of a handful of rows
    the planner is right to prefer one; what is asserted is that the index *qualifies*.
    """
    await db.execute(sa.text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        (
            await db.execute(
                sa.text("EXPLAIN SELECT 1 FROM players WHERE display_name ILIKE :p ESCAPE '\\'"),
                {"p": "%flash%"},
            )
        ).scalars()
    )
    assert "ix_players_display_name_trgm" in plan, plan


# ====================================================================== sorting and paging


async def test_sorts(client: AsyncClient, db: AsyncSession, models: None) -> None:
    short_dear = await _game(db, minute=1, plies=10, cost="0.90")
    long_cheap = await _game(db, minute=2, plies=90, cost="0.01")
    middling = await _game(db, minute=3, plies=50, cost="0.40")

    assert await _ids(client) == [str(middling), str(long_cheap), str(short_dear)]
    assert await _ids(client, sort="longest") == [str(long_cheap), str(middling), str(short_dear)]
    assert await _ids(client, sort="costliest") == [
        str(short_dear),
        str(middling),
        str(long_cheap),
    ]


async def test_keyset_pages_walk_the_archive_without_gaps(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """Five games, pages of two, under a sort where *every* game ties.

    A cursor on the sort value alone skips each game that shares the anchor's value; with
    identical ply counts that would be all of them after the first page.
    """
    for minute in range(5):
        await _game(db, minute=minute, plies=30)

    everything = await _ids(client, sort="longest")
    walked: list[str] = []
    page = await _ids(client, sort="longest", limit=2)
    while page:
        walked += page
        page = await _ids(client, sort="longest", limit=2, before=page[-1])

    assert walked == everything
    assert len(walked) == 5


async def test_after_walks_back_and_reads_the_same_way(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    ids = [await _game(db, minute=m) for m in range(5)]
    newest_first = [str(i) for i in reversed(ids)]

    older = await _ids(client, limit=2, before=newest_first[3])
    newer = await _ids(client, limit=2, after=newest_first[3])

    assert older == newest_first[4:]
    assert newer == newest_first[1:3], "the two games just above the anchor, newest first"


async def test_a_new_game_does_not_shift_the_next_page(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """What an offset gets wrong: a game starting between two page loads repeats a row."""
    for minute in range(4):
        await _game(db, minute=minute)
    first = await _ids(client, limit=2)

    await _game(db, minute=10)
    second = await _ids(client, limit=2, before=first[-1])

    assert not set(first) & set(second)


async def test_rejects_an_overlong_search(client: AsyncClient) -> None:
    assert (await client.get("/games", params={"q": "x" * 101})).status_code == 422


# ====================================================================== cost


async def test_the_archive_costs_a_fixed_number_of_queries(
    client: AsyncClient, db: AsyncSession, models: None
) -> None:
    """Every filter on at once, at one game and at seven — the count must not move.

    Each filter is an `EXISTS` inside the one `SELECT`; one that became a query of its own, or a
    per-game lookup while serialising, shows up here as growth.
    """
    params = {
        "status": ["finished", "running"],
        "result": "white",
        "kind": "models",
        "model": "acme/alpha",
        "opponent": "acme/beta",
        "q": "alpha",
        "sort": "longest",
    }
    await _game(db, minute=0)

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        assert len(await _ids(client, **params)) == 1
        one_game = len(statements)

        for minute in range(1, 7):
            await _game(db, minute=minute)
        statements.clear()

        anchor = (await _ids(client, **params))[0]
        assert len(await _ids(client, **params)) == 7
        statements.clear()
        await _ids(client, **params)
        seven_games = len(statements)

        statements.clear()
        await _ids(client, **params, before=anchor)
        paged = len(statements)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert one_game <= 2, f"the archive took {one_game} queries for one game"
    assert seven_games == one_game, (
        f"the archive grew from {one_game} queries at one game to {seven_games} at seven"
    )
    assert paged == one_game, "the cursor must resolve inside the query, not as a lookup first"
