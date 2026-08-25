"""Asking Clerk who someone is (AUTH-14).

Never reaches Clerk: the suite is free and deterministic, so the directory is driven by
hand-built payloads in the shape Clerk documents. What is asserted here is our *reading* of that
shape — which address is primary, and what happens when the answer is unusable.
"""

from __future__ import annotations

from typing import Any

import pytest

from chessmark.core.clerk import ClerkDirectory, _display_name, _primary_email


def user_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "user_abc",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "primary_email_address_id": "idn_primary",
        "email_addresses": [
            {"id": "idn_other", "email_address": "other@example.com"},
            {"id": "idn_primary", "email_address": "ada@example.com"},
        ],
        **overrides,
    }


def test_the_primary_address_wins_over_the_first_listed() -> None:
    """Clerk lists addresses in no particular order, so taking `[0]` picks an old one at random."""
    assert _primary_email(user_payload()) == "ada@example.com"


def test_the_first_address_is_used_when_none_is_marked_primary() -> None:
    payload = user_payload(primary_email_address_id=None)

    assert _primary_email(payload) == "other@example.com"


def test_no_addresses_is_no_email() -> None:
    assert _primary_email(user_payload(email_addresses=[])) is None
    assert _primary_email({}) is None


def test_a_name_is_built_from_the_parts_clerk_has() -> None:
    assert _display_name(user_payload()) == "Ada Lovelace"
    assert _display_name(user_payload(last_name=None)) == "Ada"


def test_a_username_stands_in_for_a_missing_name() -> None:
    payload = user_payload(first_name=None, last_name=None, username="countess")

    assert _display_name(payload) == "countess"


def test_no_name_at_all_is_none() -> None:
    assert _display_name(user_payload(first_name=None, last_name=None)) is None


async def test_an_unconfigured_directory_asks_nothing() -> None:
    """No secret means no calls — not an exception on a path that runs during sign-in."""
    directory = ClerkDirectory("")

    assert not directory.configured
    assert await directory.identity_of("user_abc") == (None, None)
    assert await directory.find_by_email("ada@example.com") is None


@pytest.mark.parametrize("answer", [[], [{"id": "user_a"}, {"id": "user_b"}]])
async def test_an_ambiguous_or_empty_email_lookup_returns_nobody(
    monkeypatch: pytest.MonkeyPatch, answer: list[dict[str, str]]
) -> None:
    """Zero is nobody; two is ambiguous.

    Guessing which person an operator meant is the wrong way to be helpful about someone else's
    credits — the caller gets a 404 and can be more specific.
    """
    directory = ClerkDirectory("sk_test_x")

    async def fake_get(path: str, params: Any = None) -> Any:
        return answer

    monkeypatch.setattr(directory, "_get", fake_get)

    assert await directory.find_by_email("ada@example.com") is None


async def test_exactly_one_match_is_the_person(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = ClerkDirectory("sk_test_x")

    async def fake_get(path: str, params: Any = None) -> Any:
        return [{"id": "user_abc"}]

    monkeypatch.setattr(directory, "_get", fake_get)

    assert await directory.find_by_email("ada@example.com") == "user_abc"
