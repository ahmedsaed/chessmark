"""Settings parsing.

`cors_origins` gets its own tests because both `source .env` and Docker Compose's `env_file`
strip the quotes from a JSON array, and the failure only shows up at process start.
"""

from __future__ import annotations

import pytest

from chessmark.core.config import Settings


@pytest.mark.parametrize(
    "raw",
    [
        '["http://localhost:3010","https://chessmark.com"]',
        "[http://localhost:3010,https://chessmark.com]",
        "http://localhost:3010,https://chessmark.com",
        " http://localhost:3010 , https://chessmark.com ",
    ],
)
def test_cors_origins_accepts_every_form_the_environment_produces(raw: str) -> None:
    settings = Settings(cors_origins=raw)  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://localhost:3010", "https://chessmark.com"]


def test_cors_origins_accepts_a_real_list() -> None:
    assert Settings(cors_origins=["http://a"]).cors_origins == ["http://a"]


def test_cors_origins_has_a_working_default() -> None:
    assert Settings().cors_origins == ["http://localhost:3010"]


def test_local_environment_is_flagged() -> None:
    assert Settings(environment="local").is_local
    assert not Settings(environment="production").is_local


def test_chessmark_uses_its_own_port_block() -> None:
    """See ADR-0012 — 3000/8000/5432/6379 belong to other projects on this machine."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.api_port == 8010
    assert "5433" in settings.database_url
    assert "6380" in settings.redis_url
