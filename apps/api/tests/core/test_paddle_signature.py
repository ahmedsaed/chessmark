"""Paddle's webhook signature: `ts=…;h1=…`, HMAC-SHA256 over `{ts}:{raw body}` (ADR-0055)."""

from __future__ import annotations

import hmac
from hashlib import sha256

import pytest

from chessmark.core.paddle_signature import WebhookError, sign, verify

SECRET = "pdl_ntfset_01test_secret"
BODY = b'{"event_type":"transaction.completed","data":{"id":"txn_1"}}'
NOW = 1_790_000_000


def header(body: bytes = BODY, *, secret: str = SECRET, ts: int = NOW) -> str:
    return f"ts={ts};h1={sign(secret, timestamp=str(ts), body=body)}"


def test_a_genuine_delivery_verifies() -> None:
    verify(SECRET, body=BODY, signature_header=header(), now=NOW)


def test_the_signature_is_paddles_own_construction() -> None:
    """Pinned against the documented algorithm, not against `sign` — a verifier and a signer that
    share one mistake agree with each other and with nobody else."""
    expected = hmac.new(SECRET.encode(), f"{NOW}:".encode() + BODY, sha256).hexdigest()
    verify(SECRET, body=BODY, signature_header=f"ts={NOW};h1={expected}", now=NOW)


def test_every_signature_offered_during_a_rotation_is_tried() -> None:
    stale = sign("pdl_ntfset_old", timestamp=str(NOW), body=BODY)
    current = sign(SECRET, timestamp=str(NOW), body=BODY)
    verify(SECRET, body=BODY, signature_header=f"ts={NOW};h1={stale};h1={current}", now=NOW)


@pytest.mark.parametrize(
    ("body", "signature"),
    [
        (BODY + b" ", header()),
        (BODY, header(secret="pdl_ntfset_someone_else")),
        (BODY, header(ts=NOW - 301)),
        (BODY, header(ts=NOW + 301)),
        (BODY, f"ts={NOW}"),
        (BODY, "h1=abc"),
        (BODY, "ts=soon;h1=abc"),
        (BODY, None),
    ],
    ids=[
        "tampered body",
        "another secret",
        "stale",
        "from the future",
        "no signature",
        "no timestamp",
        "timestamp not a number",
        "no header",
    ],
)
def test_a_delivery_that_is_not_paddles_is_refused(body: bytes, signature: str | None) -> None:
    with pytest.raises(WebhookError):
        verify(SECRET, body=body, signature_header=signature, now=NOW)


def test_nothing_verifies_without_a_secret() -> None:
    """An unset secret must refuse, not sign with the empty key — which anybody can do."""
    forged = f"ts={NOW};h1={sign('', timestamp=str(NOW), body=BODY)}"
    with pytest.raises(WebhookError):
        verify("", body=BODY, signature_header=forged, now=NOW)
