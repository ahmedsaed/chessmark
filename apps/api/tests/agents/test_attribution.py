"""App attribution headers, and the gate they do not lift (AGENT-18).

The temptation this file exists to settle: `thinkingmachines/inkling-small:free` refuses with 403
*"only available on agentic harnesses"*, which reads like a header we forgot. It is not — the live
endpoint answered 403 to every combination of attribution headers, and the paid variant of the same
model answered normally with none. So attribution is sent because it is worth having, and the
gated-model path is somewhere else entirely (`worker._disable_gated`).
"""

from __future__ import annotations

from typing import Any

import pytest

from chessmark.agents.attribution import attribution_headers
from chessmark.agents.llm import LlmGateway
from chessmark.agents.types import LlmError
from chessmark.core.config import Settings


def _settings(**kwargs: Any) -> Settings:
    return Settings(**kwargs)  # type: ignore[arg-type]


class TestWhatWeSend:
    def test_the_configured_url_and_title(self) -> None:
        headers = attribution_headers(
            _settings(app_url="https://chessmark.server.ahmedsaed.me", app_title="Chessmark")
        )
        assert headers == {
            "HTTP-Referer": "https://chessmark.server.ahmedsaed.me",
            "X-OpenRouter-Title": "Chessmark",
        }

    def test_a_trailing_slash_is_dropped(self) -> None:
        """Two spellings of one URL would be two app pages, and the usage would be split."""
        headers = attribution_headers(_settings(app_url="https://chessmark.example/"))
        assert headers["HTTP-Referer"] == "https://chessmark.example"

    def test_it_falls_back_to_the_first_cors_origin(self) -> None:
        """The web front end's origin is our public address, so a development machine needs no
        second variable — and production sets `APP_URL` explicitly anyway."""
        headers = attribution_headers(_settings(app_url="", cors_origins=["http://localhost:3010"]))
        assert headers["HTTP-Referer"] == "http://localhost:3010"
        assert headers["X-OpenRouter-Title"] == "Chessmark", (
            "a localhost referer is tracked only with a title"
        )

    @pytest.mark.parametrize("url", ["", "   ", "https://", "chessmark.example", "/games"])
    def test_a_placeholder_names_nobody(self, url: str) -> None:
        """A referer OpenRouter cannot resolve is worse than none: it creates an app page named
        after whatever we left in the file. A bare `https://` is the trap — `rstrip("/")` leaves
        `https:`, which is neither empty nor a URL."""
        assert attribution_headers(_settings(app_url=url, cors_origins=[])) == {}

    def test_localhost_is_not_a_placeholder(self) -> None:
        """It is where every developer runs, and OpenRouter tracks it given a title."""
        headers = attribution_headers(_settings(app_url="http://localhost:3010"))
        assert headers["HTTP-Referer"] == "http://localhost:3010"


class TestWhenTheyRide:
    """Only alongside a real credential. A scripted gateway reaches no provider, and headers on its
    requests would show up in recorded fixtures for a call that never left the process."""

    async def test_a_real_key_sends_them(self) -> None:
        seen: dict[str, Any] = {}

        async def capture(**kwargs: Any) -> Any:
            seen.update(kwargs)
            raise RuntimeError("stop here — the request is all this test wants")

        gateway = LlmGateway(
            api_key="sk-or-test",
            completion_fn=capture,
            attribution={"HTTP-Referer": "https://chessmark.example", "X-OpenRouter-Title": "C"},
        )
        with pytest.raises(LlmError):
            await gateway.complete(
                model="vendor/model", messages=[{"role": "user", "content": "x"}]
            )

        assert seen["extra_headers"] == {
            "HTTP-Referer": "https://chessmark.example",
            "X-OpenRouter-Title": "C",
        }

    async def test_a_scripted_gateway_sends_nothing(self) -> None:
        seen: dict[str, Any] = {}

        async def capture(**kwargs: Any) -> Any:
            seen.update(kwargs)
            raise RuntimeError("stop here")

        gateway = LlmGateway(
            completion_fn=capture,
            attribution={"HTTP-Referer": "https://chessmark.example"},
        )
        with pytest.raises(LlmError):
            await gateway.complete(
                model="vendor/model", messages=[{"role": "user", "content": "x"}]
            )

        assert "extra_headers" not in seen

    def test_they_are_not_a_body_field(self) -> None:
        """`session_id`, `usage` and `provider` go in `extra_body` because OpenRouter reads them
        from the body and LiteLLM would drop them. These are HTTP headers, and putting them in the
        body would be a silently ignored request field."""
        request = LlmGateway(api_key="sk-or-test").build_request(
            model="vendor/model", messages=[{"role": "user", "content": "x"}]
        )
        assert "HTTP-Referer" not in request["extra_body"]
        assert "HTTP-Referer" not in request
