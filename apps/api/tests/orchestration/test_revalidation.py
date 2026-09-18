"""Which cache tags an event invalidates, and — more importantly — which it does not.

The website caches every public read and relies on this module to tell it when an answer has
stopped being true (ADR-0046). Two failures are possible and they are not symmetric:

* **Too few tags** and a page is stale until the fallback `revalidate` expires it. Minutes of
  wrong numbers, self-healing.
* **Too many tags** and the most expensive read on the site — the leaderboard, which four pages
  wait on — is evicted on every ply of every running game. The site stays *correct* and quietly
  stops being cached at all, which looks like nothing and undoes the entire change.

The second is the one nothing else would catch, so it is the one asserted hardest here.
"""

from __future__ import annotations

import uuid

import pytest

from chessmark.db.enums import EventType
from chessmark.orchestration.revalidation import (
    GAMES,
    LEADERBOARD,
    MODELS,
    TOURNAMENTS,
    notify_web,
    tags_for,
)

GAME_ID = uuid.UUID("3f2504e0-4f89-41d3-9a0c-0305e82c3301")


def test_a_move_touches_the_game_and_the_lists_and_nothing_else() -> None:
    """A move changes a board. It does not change anybody's rating."""
    tags = tags_for(GAME_ID, [EventType.MOVE_MADE])

    assert f"game:{GAME_ID}" in tags
    assert GAMES in tags
    # The assertion this file exists for. A hundred-ply game would otherwise rebuild the ranking a
    # hundred times, and the only visible symptom would be that the site felt slow again.
    assert LEADERBOARD not in tags
    assert MODELS not in tags
    assert TOURNAMENTS not in tags


def test_reasoning_and_tool_calls_do_not_touch_the_ranking_either() -> None:
    # These are the noisiest event types by far — several per turn — so if any of them reached the
    # leaderboard tag, the cache would be evicted more often than it was read.
    tags = tags_for(GAME_ID, [EventType.THINKING, EventType.TOOL_CALLED, EventType.OUTPUT])

    assert LEADERBOARD not in tags


def test_a_finished_game_invalidates_everything_derived_from_it() -> None:
    """The one event that really does move the rest of the site."""
    tags = tags_for(GAME_ID, [EventType.MOVE_MADE, EventType.GAME_ENDED])

    for expected in (f"game:{GAME_ID}", GAMES, LEADERBOARD, MODELS, TOURNAMENTS):
        assert expected in tags


def test_a_started_game_invalidates_the_ranking_too() -> None:
    # A new game changes the counts `/about` and `/methodology` quote, and those come from
    # `/leaderboard/summary`, which carries the leaderboard tag.
    assert LEADERBOARD in tags_for(GAME_ID, [EventType.GAME_STARTED])


def test_a_pause_moves_the_lists_without_ending_anything() -> None:
    # A paused game shows a different state on its lobby card, so the lists are stale — but nothing
    # has been decided, so no rating has moved.
    tags = tags_for(GAME_ID, [EventType.GAME_PAUSED])

    assert GAMES in tags
    assert LEADERBOARD not in tags


def test_no_events_means_no_tags() -> None:
    """A caller with nothing to report must not evict anything."""
    assert tags_for(GAME_ID, []) == []


def test_tags_are_not_duplicated_when_several_events_arrive_together() -> None:
    # A turn commits a batch. Sending `leaderboard` three times would be three evictions of the
    # same entry and three round trips for one fact.
    tags = tags_for(GAME_ID, [EventType.GAME_ENDED, EventType.GAME_ENDED, EventType.MOVE_MADE])

    assert len(tags) == len(set(tags))


@pytest.mark.asyncio
async def test_notify_web_is_silent_when_unconfigured() -> None:
    """Local development and the test suite run without a web tier, and must not care.

    Asserted rather than assumed because the alternative — an exception escaping into
    `publish_events` — would fail a turn that had already committed, which is invariant 1.
    """
    await notify_web(GAME_ID, [EventType.GAME_ENDED])
