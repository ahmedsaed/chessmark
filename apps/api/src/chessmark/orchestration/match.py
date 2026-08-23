"""Creating a match and getting it moving."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.agents.registry import NoEndpointError, select_endpoint
from chessmark.agents.routing import ProviderRouting
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

    quantization: str | None = None
    """The precision this contestant plays at (ADR-0015).

    Part of the contestant's identity, not a filter: `model@fp4` and `model@fp8` are different
    contestants. `None` takes the healthiest endpoint at whatever precision, and records it.
    """

    provider: str | None = None
    """Force a specific endpoint. Normally left unset and chosen by uptime — this exists for
    telling a model's fault apart from its host's."""


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
            model_id=seat.model_id or await registry_id_for(session, seat.model),
            user_id=seat.user_id,
            persona=seat.persona,
            system_prompt_version=PROMPT_VERSION,
            sampling={"model": seat.model} if seat.model else {},
        )
        # One endpoint, pinned for the whole game (ADR-0015). Previously the router chose per
        # call, and it did switch mid-game: the first paid benchmark was served by Baidu for 70
        # calls and StreamLake for 33, so that result measures a blend nothing can reproduce.
        players[colour].provider_routing = (
            await resolve_routing(
                session,
                routing,
                seat.model,
                quantization=seat.quantization,
                provider=seat.provider,
            )
        ).to_record()

    match = Match(game=game, white=players[Colour.WHITE], black=players[Colour.BLACK])

    for colour in (Colour.WHITE, Colour.BLACK):
        # Only a model has a transcript. A human seat has no prompt, no cached prefix and no
        # tokens, and seeding one would write a system prompt nothing will ever read.
        if PlayerKind(match.player(colour).kind) is not PlayerKind.MODEL:
            continue

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
    model_slug: str | None,
    *,
    quantization: str | None = None,
    provider: str | None = None,
) -> ProviderRouting:
    """Pin one endpoint for this seat, for the whole game (ADR-0015).

    This used to *filter* precisions and leave the router to pick among what remained. That was
    the wrong shape twice over: it assumed 4-bit is not worth measuring, and it did not actually
    pin anything — the first paid benchmark was served by two different endpoints inside one game,
    so the number it produced cannot be reproduced.

    Now the seat resolves to a single provider, chosen by uptime, and `only` carries it. The
    precision comes from the contestant's identity rather than a policy: `model@fp4` is a
    contestant, not a violation.

    Falls back to the unpinned policy when there is nothing to pin against — an unregistered model,
    or one with no synced endpoints. Better a game that runs and records what served it than a
    refusal over missing bookkeeping.
    """
    if not model_slug:
        return routing

    if provider is not None:
        # Explicitly forced, usually to tell a model's fault apart from its host's.
        return replace(routing, only=(provider,), quantizations=())

    try:
        endpoint = await select_endpoint(session, model_slug=model_slug, quantization=quantization)
    except NoEndpointError:
        # Asking for a precision nothing serves is the caller's mistake and should surface as one.
        if quantization is not None:
            raise
        return routing

    # `quantizations` is cleared: the endpoint *is* the constraint now, and naming a precision as
    # well would refuse the very endpoint we just chose whenever it reports `unknown`.
    return replace(routing, only=(endpoint.provider_name,), quantizations=())


async def registry_id_for(session: AsyncSession, model_slug: str | None) -> uuid.UUID | None:
    """The `model_registry` row a slug names, if we know it.

    Complements `sampling["model"]` rather than replacing it: the slug is what the game *ran*, and
    stays readable after a rename, while this FK is what aggregate queries join on. A leaderboard
    cannot group by a JSON string, so a player left unlinked is a game the ratings cannot see.

    Returns `None` for an unknown slug instead of raising — a model absent from the registry is
    still playable, it just does not aggregate until the registry catches up.
    """
    if not model_slug:
        return None

    model_id: uuid.UUID | None = await session.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == model_slug)
    )
    return model_id


def model_for(player: Player) -> str:
    """The provider slug a player runs under.

    Stored in `sampling` so the registry row can change without rewriting history — a game must
    stay readable after a model is renamed or retired.
    """
    model = player.sampling.get("model") if player.sampling else None
    return str(model) if model else player.display_name
