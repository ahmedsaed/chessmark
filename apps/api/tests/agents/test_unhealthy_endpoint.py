"""A 400 that is about the endpoint, not the request (OPS-12..14, ADR-0017).

**The status code does not say whether the request or the endpoint is at fault. The body does.**
This is the third time that lesson has arrived: first a provider 404 classified as "does not
exist", then a 403 classified as a bad request, and now a 400 whose body reads
`DEGRADED function cannot be invoked`. Each one abandoned a real game.

The other half is what must *not* change: the context-length 400 is our own arithmetic, and it has
to keep failing fast rather than being retried and cooled down.
"""

from __future__ import annotations

import pytest

from chessmark.agents.llm import (
    endpoint_is_unhealthy,
    is_unavailable,
    rejects_the_request,
)

#: Verbatim from the game abandoned at ply 1 on 2026-08-28.
DEGRADED_400 = (
    'litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error",'
    '"code":400,"metadata":{"raw":"{\\"status\\":400,\\"title\\":\\"Bad Request\\",\\"detail\\":'
    "\\\"Function id '0a213807-640b-43fb-bfbf-2919f9b666ad': DEGRADED function cannot be "
    'invoked\\"}","provider_name":"Nvidia","is_byok":false}}}'
)

#: Verbatim from the game abandoned at ply 10 by our own unclamped `max_tokens`. Genuinely a bad
#: request, and it must stay one.
CONTEXT_400 = (
    "litellm.BadRequestError: OpenrouterException - This endpoint's maximum context length is "
    "65536 tokens. However, you requested about 65810 tokens (1430 of text input, 380 of tool "
    "input, 64000 in the output)."
)


class RejectedError(Exception):
    status_code = 400

    def __init__(self, body: str) -> None:
        super().__init__(body)


def test_a_degraded_endpoint_is_unavailability_not_a_bad_request() -> None:
    error = RejectedError(DEGRADED_400)

    assert endpoint_is_unhealthy(error)
    assert is_unavailable(error), "so the game pauses and the endpoint is cooled down"
    assert not rejects_the_request(error), "and the retry budget is not spent abandoning it"


def test_our_own_arithmetic_is_still_a_bad_request() -> None:
    """The narrow half. A 400 saying the completion does not fit is us, not them: the next attempt
    sends the same bytes and is refused the same way, so retrying is waste and pausing is a lie."""
    error = RejectedError(CONTEXT_400)

    assert not endpoint_is_unhealthy(error)
    assert not is_unavailable(error)
    assert rejects_the_request(error)


@pytest.mark.parametrize(
    "body",
    [
        "no healthy upstream",
        "Service temporarily unavailable, try again later",
        "the model is currently loading",
        "provider is overloaded",
        "insufficient capacity for this request",
    ],
)
def test_other_endpoint_health_wordings(body: str) -> None:
    """Recognising one more wording costs nothing; failing to recognise one abandons a game the way
    it does today, which is why the list errs wide within 400s and narrow across statuses."""
    assert endpoint_is_unhealthy(RejectedError(body))


def test_a_different_status_is_not_reinterpreted() -> None:
    """`endpoint_is_unhealthy` is only ever asked about a 400. A 500 is already retried, and a 200
    containing the word "degraded" is a chess comment."""

    class ServerError(Exception):
        status_code = 500

    assert not endpoint_is_unhealthy(ServerError("DEGRADED function cannot be invoked"))
