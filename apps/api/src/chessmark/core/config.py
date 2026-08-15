"""Application configuration, loaded from the environment."""

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Without this, pydantic-settings JSON-decodes list fields inside the env source and
        # raises before any validator runs — so a shell-stripped `CORS_ORIGINS` would be fatal.
        # Turning it off lets `_parse_origins` below accept whatever form actually arrives.
        enable_decoding=False,
    )

    # --- Runtime ---
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = True
    log_level: str = "INFO"

    # --- API ---
    # Ports avoid 3000/8000/5432/6379, which other local projects already use.
    api_host: str = "0.0.0.0"
    api_port: int = 8010
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3010"])

    @field_validator("allowed_quantizations", mode="before")
    @classmethod
    def _parse_quantizations(cls, value: Any) -> Any:
        return cls._parse_origins(value)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        """Accept a JSON array or a comma-separated string.

        Both `source .env` in a shell and Docker Compose's `env_file` strip the quotes from
        `["http://x"]`, leaving invalid JSON. Rather than make the file format fragile, accept
        whichever form arrives.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                text = text[1:-1] if text.endswith("]") else text[1:]

        return [origin.strip().strip("\"'") for origin in text.split(",") if origin.strip()]

    # --- Datastores ---
    # Kept as plain strings: both SQLAlchemy and redis-py want a str, and the
    # DSN types would only add a round-trip conversion at every use site.
    database_url: str = "postgresql+asyncpg://chessmark:chessmark@localhost:5433/chessmark"
    redis_url: str = "redis://localhost:6380/0"

    # --- LLM ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Auth (Clerk) ---
    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""
    clerk_jwks_url: str = ""

    # --- Cost & abuse controls ---
    max_usd_per_game: float = 1.00
    max_games_per_user_per_day: int = 20
    global_daily_usd_budget: float = 25.00

    # --- Provider routing (benchmark integrity) ---
    #: Quantizations a ranked game may be served by. Default is **8-bit and above**: a model
    #: served at 4-bit is not the model we mean to be scoring, and a leaderboard that silently
    #: mixes precisions measures the routing lottery as much as the model. `unknown` is excluded
    #: too — a provider that will not declare its precision could be serving anything.
    allowed_quantizations: list[str] = Field(
        default_factory=lambda: ["int8", "fp8", "mxfp8", "fp16", "bf16", "fp32"]
    )

    #: Endpoints slower than this are skipped. A turn already takes minutes on a slow provider;
    #: this stops the routing lottery from picking the worst one available.
    min_throughput_tps: float | None = None

    #: `price`, `throughput`, or `latency`. Price by default — the cheapest endpoint for a given
    #: model at an acceptable precision is still the same model.
    provider_sort: str = "price"

    # --- Match rules ---
    max_illegal_move_retries: int = 5
    max_moves_per_game: int = 300

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
