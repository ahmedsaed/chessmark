"""Strip credentials before anything is written to the database.

LOG-01 stores provider requests verbatim. "Verbatim" must never include the API key, and the
redaction has to happen *before* persistence rather than at display time — a leaked key in a
JSONB column is leaked whether or not the UI renders it.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

#: Keys whose values are always secret, whatever they contain. Compared case-insensitively with
#: separators stripped, so `api_key`, `api-key`, and `apiKey` all match.
SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "auth",
        "accesstoken",
        "bearer",
        "cookie",
        "clerksecretkey",
        "openrouterapikey",
        "password",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "setcookie",
        "token",
        "xapikey",
    }
)

#: Catches a key that slipped through under an unexpected field name. Covers OpenAI-style
#: (`sk-...`), OpenRouter (`sk-or-v1-...`), and Anthropic (`sk-ant-...`).
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
)

_SEPARATORS = re.compile(r"[-_\s]")


def _is_sensitive(key: str) -> bool:
    return _SEPARATORS.sub("", key).lower() in SENSITIVE_KEYS


def scrub_text(text: str) -> str:
    """Replace anything that looks like a credential inside free text."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact a payload.

    Returns a new structure; the input is never mutated, because the caller may still need the
    real request to actually make the call.
    """
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def contains_secret(value: Any) -> bool:
    """True if a payload still looks like it holds a credential. Used to assert on fixtures."""
    if isinstance(value, dict):
        return any(contains_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    return False
