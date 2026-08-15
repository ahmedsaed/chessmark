"""Creating a match and getting it moving."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.agents.registry import endpoints_for
from chessmark.agents.routing import ProviderRouting, widen_for_first_party
from chessmark.agents.tools import TOOL_SCHEMA_VERSION
from chessmark.agents.turn import ensure_system_prompt
from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, ModelRegistry, Player
from chessmark.db.repositories import add_player, append_event, create_game, get_game
from chessmark.game import ChessBoard, Colour
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue

STARTING_FEN = ChessBoard().fen


@dataclass(frozen=True, slots=True)
class Seat:
    """Who is playing, and as what."""

    display_name: str
    model: str | None = None
    kind: PlayerKind = PlayerKind.MODEL
    model_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    persona: str | None = None


@dataclass(slots=True)
class Match:
    game: Game
    white: Player
    black: Player

    def player(self, colour: Colour) -> Player:
        return self.white if colour is Colour.WHITE else self.black

    def opponent(self, colour: Colour) -> Player:
        return self.player(colour.opponent)


async def create_match(
    session: AsyncSession,
    *,
    white: Seat,
    black: Seat,
    start_fen: str = STARTING_FEN,
    is_ranked: bool = False,
    trash_talk_enabled: bool = True,
    max_illegal_retries: int = 5,
    max_plies: int = 300,
    max_usd: Decimal | None = None,
    created_by_user_id: uuid.UUID | None = None,
    routing: ProviderRouting | None = None,
) -> Match:
    """Create a game, seat both players, and seed both transcripts.

    Both system prompts are written here rather than lazily on each player's first turn, so that
    an opening taunt delivered before Black has ever moved cannot land ahead of Black's system
    prompt — the row that heads its cached prefix (ADR-0003).

    Ranked games are forced non-conversational regardless of what the caller asked for (TALK-03):
    a ranked result contaminated by banter is not comparable with one that was not.
    """
    if is_ranked:
        trash_talk_enabled = False

    # A ranked game must never be served by whatever endpoint the router feels like. The policy
    # is stored so the result can always say what precision it was played at (BENCH-04).
    routing = routing or ProviderRouting()
    routing = await resolve_routing(session, routing, [white.model, black.model])

    game = await create_game(
        session,
        start_fen=start_fen,
        is_ranked=is_ranked,
        trash_talk_enabled=trash_talk_enabled,
        max_illegal_retries=max_illegal_retries,
        max_plies=max_plies,
        max_usd=max_usd,
        created_by_user_id=created_by_user_id,
        prompt_version=PROMPT_VERSION,
        tool_schema_version=TOOL_SCHEMA_VERSION,
    )
    game.provider_routing = routing.to_record()

    players: dict[Colour, Player] = {}
    for colour, seat in ((Colour.WHITE, white), (Colour.BLACK, black)):
        players[colour] = await add_player(
            session,
            game_id=game.id,
            colour=colour,
            kind=seat.kind,
            display_name=seat.display_name,
            model_id=seat.model_id,
            user_id=seat.user_id,
            persona=seat.persona,
            system_prompt_version=PROMPT_VERSION,
            sampling={"model": seat.model} if seat.model else {},
        )

    match = Match(game=game, white=players[Colour.WHITE], black=players[Colour.BLACK])

    for colour in (Colour.WHITE, Colour.BLACK):
        await ensure_system_prompt(
            session,
            game=game,
            player=match.player(colour),
            opponent_name=match.opponent(colour).display_name,
        )

    return match


async def start_match(
    session: AsyncSession,
    queue: TurnQueue,
    *,
    game_id: uuid.UUID,
) -> AdvanceTurn:
    """Mark a game running and enqueue its first turn.

    The job is enqueued by the caller *after* the transaction commits — see `worker.start_game`.
    Enqueuing inside the transaction would let a worker pick up a game that does not exist yet if
    the transaction later rolled back.
    """
    game = await get_game(session, game_id)
    game.status = GameStatus.RUNNING
    game.started_at = sa.func.now()

    await append_event(
        session,
        game_id=game.id,
        type=EventType.GAME_STARTED,
        payload={
            "start_fen": game.start_fen,
            "is_ranked": game.is_ranked,
            "trash_talk_enabled": game.trash_talk_enabled,
            "prompt_version": game.prompt_version,
            "tool_schema_version": game.tool_schema_version,
        },
    )
    await session.flush()

    return AdvanceTurn(game_id=game.id, expected_ply=game.ply_count)


async def resolve_routing(
    session: AsyncSession,
    routing: ProviderRouting,
    model_slugs: list[str | None],
) -> ProviderRouting:
    """Adjust the policy for the models actually playing.

    A closed-weight model reports `unknown` because there is nothing to disclose, so the strict
    default would make it unplayable. `widen_for_first_party` admits `unknown` only when that is
    the sole option *and* only from the model's own vendor. If either seat needs the widening,
    both get it — a game runs under one policy, and recording two would make the result
    ambiguous.
    """
    for slug in model_slugs:
        if not slug:
            continue

        model = await session.scalar(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug)
        )
        if model is None:
            continue

        pairs = [(e.provider_name, e.quantization) for e in await endpoints_for(session, model.id)]
        if not pairs:
            continue

        widened = widen_for_first_party(routing, model_slug=slug, endpoints=pairs)
        if widened is not routing:
            routing = widened

    return routing


def model_for(player: Player) -> str:
    """The provider slug a player runs under.

    Stored in `sampling` so the registry row can change without rewriting history — a game must
    stay readable after a model is renamed or retired.
    """
    model = player.sampling.get("model") if player.sampling else None
    return str(model) if model else player.display_name
