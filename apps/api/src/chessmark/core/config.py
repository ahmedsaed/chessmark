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
    #: Expected `iss` claim. Empty disables the issuer check — acceptable locally, and checked at
    #: startup in production, because a token from someone else's Clerk instance is a valid token
    #: signed by a real key and the issuer is the only thing that distinguishes it.
    clerk_issuer: str = ""
    #: Clerk's webhook signing secret (`whsec_...`). Without it the webhook cannot be trusted and
    #: refuses every delivery.
    clerk_webhook_secret: str = ""

    # --- Cost & abuse controls ---
    max_usd_per_game: float = 1.00
    max_games_per_user_per_day: int = 20
    #: Per-user daily spend ceiling. 0 disables it, leaving the game-count quota in charge.
    max_usd_per_user_per_day: float = 5.00
    global_daily_usd_budget: float = 25.00

    #: The smallest context window a model may be registered with (AGENT-14).
    #:
    #: Measured, not guessed: the transcript is resent whole every turn and grows **~1,818 tokens
    #: per ply**, so a turn's prompt *is* the context it needs. 128k covers roughly 70 plies,
    #: against a real-game median of 39.
    #:
    #: A floor, not a guarantee. No threshold makes a 300-ply game safe — that would need ~545k and
    #: would exclude almost the whole field. This removes models that cannot get started; the ply
    #: cap and the spend cap still bound the rest. 0 disables the check.
    min_context_tokens: int = 128_000

    #: Rate limiting on money-spending endpoints. 0 disables it.
    rate_limit_per_window: int = 10
    rate_limit_window_seconds: float = 60.0

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

    #: The ply cap is a **cost** bound, not a rules bound. Chessmark applies threefold repetition
    #: and the fifty-move rule automatically, so every game terminates on its own; nothing here is
    #: needed to guarantee that. What the cap prevents is a pathological game spending money for
    #: hours.
    #:
    #: 300 plies (150 moves) covers essentially every real game — an average master game is about
    #: 40 moves, and the longest competitive game on record is 269. Setting it near the median
    #: instead makes the harness, rather than chess, decide half the results: the first paid
    #: benchmark ran at 80 and ended `ply_cap` with one side a queen and rook up.
    #:
    #: Per-game USD is the bound that should actually bind. Both `ply_cap` and `budget_exceeded`
    #: are non-results and belong outside the ratings (Phase 12).
    max_moves_per_game: int = 300

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    def production_problems(self) -> list[str]:
        """Settings that are tolerable locally and dangerous in production.

        Returned rather than raised so the caller decides — the checks are useful to print in a
        deploy log even when they are not fatal.

        The failure this prevents is specific: with no JWKS URL the verifier refuses every token,
        so the app *fails closed* and nobody can start a game. That is safe but silent, and the
        symptom ("nothing works") points nowhere near the cause. Refusing to boot names it.
        """
        problems: list[str] = []

        if not self.clerk_jwks_url:
            problems.append("CLERK_JWKS_URL is not set — no request could ever authenticate")
        if not self.clerk_issuer:
            # A token from someone else's Clerk instance is signed by a real key and verifies
            # against their JWKS. The issuer is the only claim that says it was minted for us.
            problems.append("CLERK_ISSUER is not set — tokens from any Clerk instance would pass")
        if not self.clerk_webhook_secret:
            problems.append("CLERK_WEBHOOK_SECRET is not set — user updates cannot be received")
        if self.global_daily_usd_budget <= 0:
            problems.append("GLOBAL_DAILY_USD_BUDGET is unset — spending would be uncapped")
        if self.debug:
            problems.append("DEBUG is on — it exposes /docs and verbose errors")

        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
