"""Clerk webhook signature verification.

Clerk signs webhooks with Svix's scheme. It is plain HMAC-SHA256 over
`{id}.{timestamp}.{body}` — no key exchange, no novel construction — so it is implemented here
rather than pulled in as a dependency. The parts that are easy to get wrong are all guarded:

- **Constant-time comparison.** `==` on a signature leaks its prefix through timing, which is
  enough to forge one byte at a time.
- **The raw body, not the parsed one.** Re-serialising JSON changes whitespace and key order, so
  the signature would never match — and the temptation is then to stop checking it.
- **A timestamp window.** Without one, a single captured delivery can be replayed forever. Clerk
  retries failed deliveries, so a valid signature stays valid until it ages out.
- **Every offered signature is tried.** Svix sends more than one during a secret rotation, and
  checking only the first breaks every rotation.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

#: How far a delivery's timestamp may be from now. Svix's own default, and a reasonable trade
#: between clock skew and replay window.
TOLERANCE_SECONDS = 300


class WebhookError(Exception):
    """A delivery could not be verified. Never say which check failed — an attacker tuning a
    forgery learns from the difference between 'bad timestamp' and 'bad signature'."""


def _secret_bytes(secret: str) -> bytes:
    """Decode a `whsec_...` secret.

    The prefix is a label, not part of the key. Signing with it included produces a signature that
    never matches and gives no hint why.
    """
    if not secret:
        raise WebhookError("webhook secret is not configured")

    raw = secret.removeprefix("whsec_")
    try:
        return base64.b64decode(raw)
    except Exception as error:
        raise WebhookError("webhook secret is not valid base64") from error


def sign(secret: str, *, message_id: str, timestamp: str, body: bytes) -> str:
    """The signature Svix would send. Used by the verifier and by tests that forge deliveries."""
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(_secret_bytes(secret), signed, sha256).digest()
    return base64.b64encode(digest).decode()


def verify(
    secret: str,
    *,
    body: bytes,
    message_id: str | None,
    timestamp: str | None,
    signature_header: str | None,
    now: float | None = None,
) -> None:
    """Raise `WebhookError` unless this delivery is genuinely from Clerk."""
    if not message_id or not timestamp or not signature_header:
        raise WebhookError("delivery is missing its signature headers")

    try:
        sent_at = int(timestamp)
    except ValueError as error:
        raise WebhookError("delivery timestamp is not a number") from error

    drift = abs((now if now is not None else time.time()) - sent_at)
    if drift > TOLERANCE_SECONDS:
        # Covers both a stale replay and a delivery from the future, which is what a forged
        # timestamp looks like when someone is trying to buy an unlimited replay window.
        raise WebhookError("delivery is outside the accepted time window")

    expected = sign(secret, message_id=message_id, timestamp=timestamp, body=body)

    for offered in signature_header.split():
        version, _, value = offered.partition(",")
        if version != "v1":
            continue
        if hmac.compare_digest(value, expected):
            return

    raise WebhookError("signature does not match")
