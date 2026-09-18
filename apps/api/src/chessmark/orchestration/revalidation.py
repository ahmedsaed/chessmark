"""Telling the website that one of its cached answers just stopped being true.

The frontend caches every public read and tags it (`apps/web/src/lib/cache-tags.ts`). The tags are
the mechanism and the `revalidate` seconds beside them are only a backstop, which makes this module
the thing that actually keeps the site correct: a game writes a `game_events` row (invariant 7),
and the tags that row invalidates are named here and posted to the web tier. See ADR-0046.

**Best effort, always after the commit, and never able to fail a caller.** This is the same
contract `publish_events` holds and for the same reason: Postgres already has the truth, and a
derived cache must not be able to roll back a real result (invariant 1). Every failure is swallowed
and logged. The worst a lost POST can do is leave a page stale until the fallback `revalidate`
expires it, which is minutes, not forever.

**No secret configured means no notifications, not an error.** Local development and the test suite
run without one; they simply fall back to the clock.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

import httpx

from chessmark.core.config import get_settings
from chessmark.db.enums import EventType

log = logging.getLogger(__name__)

#: Anything derived from the set of games: the lobby lists, the archive, the replay pool.
GAMES = "games"
#: The stored ranking (ADR-0032) and the summary counts beside it.
LEADERBOARD = "leaderboard"
#: The catalogue and every model's aggregates.
MODELS = "models"
#: The tournament index and every event's table.
TOURNAMENTS = "tournaments"

#: The events that change something beyond the game's own page.
#:
#: A move changes the board and nothing else; the leaderboard, a model's record and a tournament
#: table all move only when a game **ends**. Invalidating the ranking on every ply would evict the
#: most expensive read on the site a hundred times a game to no purpose — and it is the read four
#: pages wait on.
_SITE_WIDE = {EventType.GAME_ENDED, EventType.GAME_STARTED}

#: A ceiling on the POST. The web tier is a container away and this runs after a commit that has
#: already succeeded, so the only thing waiting on it is the worker's next turn.
_TIMEOUT_S = 5.0


def tags_for(game_id: uuid.UUID, event_types: Iterable[str]) -> list[str]:
    """Which cache tags the given events invalidate.

    Split out from the POST so it can be tested without a web tier, and so the rule about what a
    move does and does not touch lives somewhere a reader can find it.
    """
    types = {str(t) for t in event_types}
    if not types:
        return []

    # Always: this game's own page, and the lists it appears in. A game's status shows on its lobby
    # card, so a pause or a resume changes the lists without ending anything.
    tags = [f"game:{game_id}", GAMES]

    if types & {str(t) for t in _SITE_WIDE}:
        tags += [LEADERBOARD, MODELS, TOURNAMENTS]

    return tags


async def notify_web(game_id: uuid.UUID, event_types: Iterable[str]) -> None:
    """Post the invalidated tags to the website. Never raises."""
    settings = get_settings()
    secret = settings.revalidate_secret
    origin = settings.web_origin

    if not secret or not origin:
        return

    tags = tags_for(game_id, event_types)
    if not tags:
        return

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.post(
                f"{origin}/api/revalidate",
                json={"tags": tags},
                headers={"authorization": f"Bearer {secret}"},
            )
        if response.status_code >= 400:
            # Logged rather than raised, but logged loudly: a 401 here means the two halves
            # disagree about the secret, and the symptom — a leaderboard that is five minutes
            # behind — looks like nothing at all until somebody notices the numbers lag.
            log.warning("revalidation refused: %s %s", response.status_code, response.text[:200])
    except Exception as exc:  # a cache hint must never break a turn that already committed
        log.warning("revalidation failed for %s: %s", game_id, exc)
