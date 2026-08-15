"""Credential redaction.

LOG-01 stores provider payloads verbatim, and those rows are shown in the replay UI. A key that
reaches the database is leaked whether or not anything renders it, so redaction happens before
persistence rather than at display time.
"""

from __future__ import annotations

import pytest

from chessmark.agents.redaction import REDACTED, contains_secret, redact, scrub_text

OPENROUTER_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"
ANTHROPIC_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.mark.parametrize(
    "key",
    ["api_key", "API_KEY", "apiKey", "api-key", "Authorization", "x-api-key", "cookie", "token"],
)
def test_sensitive_keys_are_redacted_whatever_their_case_or_separator(key: str) -> None:
    assert redact({key: "anything at all"})[key] == REDACTED


def test_non_sensitive_keys_survive() -> None:
    payload = {"model": "openai/gpt-oss-20b:free", "temperature": 0.7, "max_tokens": 100}
    assert redact(payload) == payload


@pytest.mark.parametrize("secret", [OPENROUTER_KEY, ANTHROPIC_KEY])
def test_keys_are_scrubbed_out_of_free_text(secret: str) -> None:
    """A key can arrive under an unexpected field name, or inside a message the model echoed."""
    assert secret not in scrub_text(f"failed with {secret} — retry")
    assert REDACTED in scrub_text(f"failed with {secret} — retry")


def test_bearer_headers_are_scrubbed() -> None:
    scrubbed = scrub_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in scrubbed


def test_nested_structures_are_redacted_throughout() -> None:
    payload = {
        "messages": [{"role": "user", "content": f"key: {OPENROUTER_KEY}"}],
        "headers": {"Authorization": f"Bearer {OPENROUTER_KEY}"},
        "nested": {"deep": {"api_key": OPENROUTER_KEY}},
        "list_of_lists": [[{"token": "secret"}]],
    }

    result = redact(payload)

    assert not contains_secret(result)
    assert OPENROUTER_KEY not in str(result)
    assert result["headers"]["Authorization"] == REDACTED
    assert result["nested"]["deep"]["api_key"] == REDACTED
    assert result["list_of_lists"][0][0]["token"] == REDACTED


def test_redaction_does_not_mutate_the_input() -> None:
    """The caller still needs the real request to actually make the call."""
    original = {"api_key": OPENROUTER_KEY, "messages": [{"content": "hi"}]}
    redact(original)

    assert original["api_key"] == OPENROUTER_KEY


def test_short_sk_prefixed_strings_are_left_alone() -> None:
    """`sk-` is not automatically a credential; over-redacting hides real content."""
    assert scrub_text("sk-short") == "sk-short"


@pytest.mark.parametrize("value", [None, 42, 3.14, True, [], {}])
def test_non_strings_pass_through(value: object) -> None:
    assert redact(value) == value


def test_contains_secret_finds_keys_anywhere() -> None:
    assert contains_secret({"a": [{"b": OPENROUTER_KEY}]})
    assert contains_secret([OPENROUTER_KEY])
    assert not contains_secret({"a": [{"b": "harmless"}]})
    assert not contains_secret(None)
