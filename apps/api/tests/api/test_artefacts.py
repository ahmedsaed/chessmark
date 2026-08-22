"""Take-away artefacts: the PGN export and the raw transcript.

Both exist to make a result checkable by someone who does not trust us. The PGN has to open in
other people's software, and the raw payloads have to be the ones the provider actually returned —
so the tests here check portability and verbatimness rather than shape.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.models import LlmCall, Turn
from chessmark.game.pgn import final_position_from_pgn
from tests.orchestration.conftest import Fixture, both_sides, run_next

pytestmark = pytest.mark.integration


async def _play(fixture: Fixture, make_worker, white: list[str], black: list[str]) -> None:
    worker = make_worker(both_sides(white, black))
    for _ in range(len(white) + len(black)):
        if await run_next(worker, fixture.queue) is None:
            break


async def _resign(fixture: Fixture, make_worker, reasoning: str = "lost") -> None:
    worker = make_worker(scripted(step(tool_call("resign"), reasoning=reasoning)))
    await run_next(worker, fixture.queue)


# ====================================================================== pgn


async def test_the_pgn_replays_to_the_position_the_server_holds(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """The point of the export. If a PGN opens in Lichess showing a different position from the
    one on the game page, every number attached to that game is unverifiable."""
    await _play(game, make_worker, ["e4", "Nf3"], ["e5", "Nc6"])

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text
    detail = (await client.get(f"/games/{game.game.id}")).json()

    assert final_position_from_pgn(pgn) == detail["current_fen"]


async def test_the_pgn_carries_the_moves_in_order(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    await _play(game, make_worker, ["e4", "Nf3"], ["e5", "Nc6"])

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text

    assert "1. e4 e5 2. Nf3 Nc6" in pgn


async def test_the_pgn_records_who_played_and_under_what(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    """Provenance is the difference between a game record and a curiosity: a PGN nobody can trace
    back to a prompt version cannot be reproduced."""
    await _play(game, make_worker, ["e4"], ["e5"])

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text

    assert '[White "white-model"]' in pgn
    assert '[Black "black-model"]' in pgn
    assert f'[ChessmarkGameId "{game.game.id}"]' in pgn
    assert "[ChessmarkPromptVersion " in pgn
    assert "[ChessmarkToolSchemaVersion " in pgn


async def test_the_pgn_reports_illegal_attempts(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    """The benchmark's headline number travels with the file."""
    worker = make_worker(
        scripted(step(tool_call("make_move", move="Ke4"), tool_call("make_move", move="e4")))
    )
    await run_next(worker, game.queue)

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text

    assert '[ChessmarkWhiteIllegalAttempts "1"]' in pgn


async def test_an_unfinished_game_exports_with_an_open_result(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    """`*` is legal PGN for a game in progress. Refusing the export would make a live game
    un-shareable for no reason."""
    await _play(game, make_worker, ["e4"], ["e5"])

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text

    assert '[Result "*"]' in pgn


async def test_a_finished_game_exports_its_result_and_termination(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    await _resign(game, make_worker)

    pgn = (await client.get(f"/games/{game.game.id}/pgn")).text

    assert '[Result "0-1"]' in pgn
    assert '[Termination "resignation"]' in pgn


async def test_the_pgn_is_served_as_a_named_download(
    client: AsyncClient, game: Fixture, make_worker
) -> None:
    """A browser must save it as a `.pgn`, not render it as a wall of text — the file only reaches
    Lichess or SCID if it arrives as a file."""
    await _play(game, make_worker, ["e4"], ["e5"])

    response = await client.get(f"/games/{game.game.id}/pgn")

    assert response.headers["content-type"].startswith("application/x-chess-pgn")
    assert f'filename="chessmark-{game.game.id}.pgn"' in response.headers["content-disposition"]


async def test_an_unknown_game_has_no_pgn(client: AsyncClient) -> None:
    response = await client.get("/games/00000000-0000-0000-0000-000000000000/pgn")

    assert response.status_code == 404


# ====================================================================== raw transcript


async def test_raw_payloads_are_withheld_while_the_game_is_live(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Invariant 8. The raw response carries the reasoning trace, so an open raw endpoint would
    route straight around the rule `/turns` enforces."""
    worker = make_worker(
        scripted(step(tool_call("make_move", move="e4"), reasoning="I intend Qh5 next."))
    )
    await run_next(worker, game.queue)

    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    response = await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")

    assert response.status_code == 409
    assert "Qh5" not in response.text


async def test_raw_payloads_are_published_once_the_game_ends(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    await _resign(game, make_worker, reasoning="This position is lost.")

    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    body = (await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")).json()

    assert len(body) == 1
    assert body[0]["reasoning_text"] == "This position is lost."


async def test_the_payloads_are_the_ones_that_were_stored(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Verbatim means verbatim (LOG-01): the endpoint must not reshape, filter, or summarise."""
    await _resign(game, make_worker)

    db.expunge_all()
    stored = (await db.scalars(sa.select(LlmCall))).all()
    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    body = (await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")).json()

    assert body[0]["request"] == stored[0].request
    assert body[0]["response"] == stored[0].response


async def test_the_request_carries_the_full_transcript_not_a_summary(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """ADR-0003's transcript is the thing a sceptical reader most wants to see — it is what makes
    the cache numbers and the token counts explicable."""
    await _resign(game, make_worker)

    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    body = (await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")).json()

    messages = body[0]["request"]["messages"]
    assert messages[0]["role"] == "system"
    assert len(messages) >= 2


async def test_no_credential_survives_into_the_response(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Redaction happens at write time, so this asserts the endpoint cannot undo it (LOG-01)."""
    await _resign(game, make_worker)

    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    text = (await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")).text.lower()

    for secret in ("api_key", "authorization", "sk-or-", "bearer "):
        assert secret not in text


async def test_calls_arrive_in_the_order_they_were_made(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """A turn that retried an illegal move makes several calls, and reading them out of order
    would misrepresent what the model did."""
    worker = make_worker(
        scripted(
            step(tool_call("make_move", move="Ke4")),
            step(tool_call("resign")),
        )
    )
    await run_next(worker, game.queue)

    turn_id = await db.scalar(sa.select(Turn.id).where(Turn.game_id == game.game.id))
    body = (await client.get(f"/games/{game.game.id}/turns/{turn_id}/raw")).json()

    assert [call["sequence"] for call in body] == sorted(call["sequence"] for call in body)
    assert len(body) >= 2


async def test_a_turn_from_another_game_is_not_reachable(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker, queue
) -> None:
    """Scoped in the query, not checked afterwards — a turn id from elsewhere must read as absent
    rather than reveal that it exists."""
    from tests.support import seat_match

    await _resign(game, make_worker)
    other = await seat_match(db, queue)
    await _resign(other, make_worker)

    stolen = await db.scalar(sa.select(Turn.id).where(Turn.game_id == other.game.id))
    response = await client.get(f"/games/{game.game.id}/turns/{stolen}/raw")

    assert response.status_code == 404


async def test_an_unknown_turn_is_a_404(client: AsyncClient, game: Fixture, make_worker) -> None:
    await _resign(game, make_worker)

    assert (await client.get(f"/games/{game.game.id}/turns/999999/raw")).status_code == 404
