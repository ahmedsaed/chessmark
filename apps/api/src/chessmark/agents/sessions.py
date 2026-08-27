"""What a session is, for OpenRouter's purposes: one game.

OpenRouter groups the generations that share a `session_id`, so the unit chosen here decides what
somebody debugging a game actually sees on the dashboard. A **match** is that unit — both seats,
every retry, every tool round-trip, in the order they happened. A turn would fragment one game
into sixty sessions; a tournament would merge a hundred games into one.

Both seats share a single id on purpose, because a session is meant to be the conversation and
half a chess game is not one. That costs one thing worth knowing: OpenRouter also uses
`session_id` as its **sticky routing key**, so a session spanning two different models expresses a
preference only one of them can satisfy. It is harmless here, by construction rather than by luck
— a ranked seat pins `only=[provider]` (ADR-0015) and an explicit constraint outranks a sticky
preference, while an unpinned seat keeps `allow_fallbacks`, so a provider that cannot serve the
other model falls back instead of failing. Should that ever stop being true, the fix is
`f"game-{game_id}-{colour}"` and two sessions per match.

The id is **derived, never stored.** `games.id` already is the session, so a column would be a
second copy of one fact and a chance for the two to disagree. It reaches the log regardless: every
`llm_calls` row keeps the request that carried it.
"""

from __future__ import annotations

from uuid import UUID


def session_for_game(game_id: UUID) -> str:
    """The OpenRouter session id for a game.

    Prefixed rather than a bare UUID, so the id is recognisable on a dashboard belonging to an
    account that may be used for other things. OpenRouter allows 256 characters; this is 41.
    """
    return f"game-{game_id}"


__all__ = ["session_for_game"]
