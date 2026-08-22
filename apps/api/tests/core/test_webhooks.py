"""Webhook signature verification, treated as adversarial input.

A webhook endpoint is an unauthenticated POST that mutates the user table. If the signature check
is wrong, anyone who finds the URL can delete accounts.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from chessmark.core.webhooks import TOLERANCE_SECONDS, WebhookError, sign, verify

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-secret-32-bytes!!").decode()
BODY = json.dumps({"type": "user.created", "data": {"id": "user_1"}}).encode()
MESSAGE_ID = "msg_2abc"


def delivery(*, secret: str = SECRET, body: bytes = BODY, at: int | None = None) -> dict[str, str]:
    timestamp = str(at if at is not None else int(time.time()))
    return {
        "message_id": MESSAGE_ID,
        "timestamp": timestamp,
        "signature_header": f"v1,{sign(secret, message_id=MESSAGE_ID, timestamp=timestamp, body=body)}",
    }


# ====================================================================== the happy path


def test_a_genuine_delivery_verifies() -> None:
    verify(SECRET, body=BODY, **delivery())


def test_several_offered_signatures_are_all_tried() -> None:
    """Svix sends more than one during a secret rotation. Checking only the first breaks every
    rotation, which is the sort of bug that surfaces at the worst moment."""
    sent = delivery()
    genuine = sent["signature_header"].removeprefix("v1,")
    bogus = base64.b64encode(b"z" * 32).decode()

    # The real signature second, so a verifier that stops after the first one fails here.
    sent["signature_header"] = f"v1,{bogus} v1,{genuine}"

    verify(SECRET, body=BODY, **sent)


# ====================================================================== forgery


def test_a_forged_signature_is_rejected() -> None:
    sent = delivery()
    sent["signature_header"] = "v1," + base64.b64encode(b"x" * 32).decode()

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **sent)


def test_a_delivery_signed_with_another_secret_is_rejected() -> None:
    other = "whsec_" + base64.b64encode(b"a-different-secret-of-32-bytes!!").decode()

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **delivery(secret=other))


def test_a_tampered_body_is_rejected() -> None:
    """The signature covers the body. Someone editing `user.deleted` into a captured
    `user.created` must not get a valid delivery out of it."""
    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY.replace(b"created", b"deleted"), **delivery())


def test_a_signature_from_another_message_is_rejected() -> None:
    """The message id is part of the signed content, so signatures cannot be moved between
    deliveries."""
    sent = delivery()
    sent["message_id"] = "msg_somebody_elses"

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **sent)


def test_an_unknown_signature_version_is_not_accepted() -> None:
    """A future `v2` we do not understand must not be waved through as though it verified."""
    sent = delivery()
    sent["signature_header"] = sent["signature_header"].replace("v1,", "v2,")

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **sent)


# ====================================================================== replay


def test_a_stale_delivery_is_rejected() -> None:
    """Clerk retries, so a captured delivery stays signature-valid indefinitely. The timestamp
    window is the only thing that stops one being replayed forever."""
    old = int(time.time()) - TOLERANCE_SECONDS - 60

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **delivery(at=old))


def test_a_delivery_from_the_future_is_rejected() -> None:
    """What a forged timestamp looks like when someone is buying themselves a long replay window."""
    ahead = int(time.time()) + TOLERANCE_SECONDS + 60

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **delivery(at=ahead))


def test_a_delivery_inside_the_window_is_accepted() -> None:
    recent = int(time.time()) - (TOLERANCE_SECONDS - 30)

    verify(SECRET, body=BODY, **delivery(at=recent))


def test_a_non_numeric_timestamp_is_rejected() -> None:
    sent = delivery()
    sent["timestamp"] = "not-a-timestamp"

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **sent)


# ====================================================================== missing pieces


@pytest.mark.parametrize("missing", ["message_id", "timestamp", "signature_header"])
def test_a_delivery_missing_a_header_is_rejected(missing: str) -> None:
    sent = delivery()
    sent[missing] = ""

    with pytest.raises(WebhookError):
        verify(SECRET, body=BODY, **sent)


def test_an_unconfigured_secret_refuses_everything() -> None:
    """Fail closed. An endpoint that accepts anything because nobody set a secret is worse than
    one that is switched off."""
    with pytest.raises(WebhookError):
        verify("", body=BODY, **delivery())


def test_the_prefix_is_not_part_of_the_key() -> None:
    """`whsec_` is a label. Signing with it included yields a signature that never matches, and no
    hint as to why."""
    raw = SECRET.removeprefix("whsec_")

    assert sign(SECRET, message_id="a", timestamp="1", body=b"x") == sign(
        raw, message_id="a", timestamp="1", body=b"x"
    )
