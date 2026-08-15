"""Application configuration, loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
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

    # --- Match rules ---
    max_illegal_move_retries: int = 5
    max_moves_per_game: int = 300

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
