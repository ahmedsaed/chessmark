"""Paddle webhook signature verification (ADR-0055).

Paddle signs each delivery with HMAC-SHA256 over `{ts}:{raw body}`, keyed with the notification
destination's secret used **as it is** — `pdl_ntfset_...`, prefix and all, unlike Svix's secret in
`core.webhooks`, whose prefix is a label. The header is `Paddle-Signature: ts=...;h1=...`, hex.

Implemented here rather than through Paddle's SDK for the reason `core.webhooks` gives: it is one
HMAC, and the parts that are easy to get wrong are the ones guarded here —

- **Constant-time comparison**, so a signature cannot be forged a byte at a time from timings.
- **The raw body**, since re-serialised JSON never matches.
- **A timestamp window.** A captured delivery is otherwise replayable forever. It is Svix's five
  minutes rather than Paddle's SDK's five seconds: a retry is re-signed, so the window only has to
  cover clock skew, and a server clock a few seconds out would otherwise refuse every purchase
  until Paddle gave up. A replay inside the window is harmless anyway — every handler is idempotent.
- **Every `h1` is tried**, because Paddle sends one per secret while a secret is being rotated.
"""

from __future__ import annotations

import hmac
import time
from hashlib import sha256

from chessmark.core.webhooks import TOLERANCE_SECONDS, WebhookError


def sign(secret: str, *, timestamp: str, body: bytes) -> str:
    """The `h1` Paddle would send. Used by the verifier and by tests that forge deliveries."""
    return hmac.new(secret.encode(), f"{timestamp}:".encode() + body, sha256).hexdigest()


def verify(
    secret: str, *, body: bytes, signature_header: str | None, now: float | None = None
) -> None:
    """Raise `WebhookError` unless this delivery is genuinely from Paddle."""
    if not secret:
        raise WebhookError("webhook secret is not configured")
    if not signature_header:
        raise WebhookError("delivery is missing its signature header")

    timestamp: str | None = None
    offered: list[str] = []
    for part in signature_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == "ts":
            timestamp = value
        elif key == "h1":
            offered.append(value)

    if timestamp is None or not offered:
        raise WebhookError("signature header is malformed")
    try:
        sent_at = int(timestamp)
    except ValueError as error:
        raise WebhookError("delivery timestamp is not a number") from error

    if abs((now if now is not None else time.time()) - sent_at) > TOLERANCE_SECONDS:
        raise WebhookError("delivery is outside the accepted time window")

    expected = sign(secret, timestamp=timestamp, body=body)
    if not any(hmac.compare_digest(value, expected) for value in offered):
        raise WebhookError("signature does not match")


__all__ = ["WebhookError", "sign", "verify"]
