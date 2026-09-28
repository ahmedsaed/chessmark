"""The production startup guard.

Phase 9 is a hard gate: nothing is deployed publicly until these controls exist. This is the code
that makes the gate mechanical rather than a line in a document somebody has to remember.
"""

from __future__ import annotations

import pytest

from chessmark.core.config import Settings
from chessmark.main import InsecureConfigurationError, create_app

SAFE = {
    "environment": "production",
    "debug": False,
    "clerk_jwks_url": "https://clerk.chessmark.com/.well-known/jwks.json",
    "clerk_issuer": "https://clerk.chessmark.com",
    "clerk_webhook_secret": "whsec_abc",
    "global_daily_usd_budget": 25.0,
    # Pinned empty, so the machine's own `.env` cannot half-configure selling under the test.
    "paddle_webhook_secret": "",
    "paddle_api_key": "",
    "paddle_product_id": "",
}


def test_a_fully_configured_production_setup_has_no_problems() -> None:
    assert Settings(**SAFE).production_problems() == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("clerk_jwks_url", "", "CLERK_JWKS_URL"),
        ("clerk_issuer", "", "CLERK_ISSUER"),
        ("clerk_webhook_secret", "", "CLERK_WEBHOOK_SECRET"),
        ("global_daily_usd_budget", 0.0, "GLOBAL_DAILY_USD_BUDGET"),
        ("debug", True, "DEBUG"),
    ],
)
def test_each_missing_control_is_reported(field: str, value: object, expected: str) -> None:
    problems = Settings(**{**SAFE, field: value}).production_problems()

    assert any(expected in problem for problem in problems), problems


def test_the_issuer_check_is_not_optional_in_production() -> None:
    """A token from someone else's Clerk instance is signed by a real key and verifies against
    their JWKS. The issuer is the only claim that says it was minted for us."""
    problems = Settings(**{**SAFE, "clerk_issuer": ""}).production_problems()

    assert any("any Clerk instance" in problem for problem in problems)


def test_local_development_is_not_held_to_this(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requiring a Clerk account to run the test suite or play a scripted game locally would make
    the project much harder to work on for no security benefit."""
    from chessmark.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "local")

    app = create_app()

    assert app is not None
    get_settings.cache_clear()


def test_production_refuses_to_start_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly at boot rather than quietly at every request. Unconfigured, the verifier
    refuses every token — safe, but the symptom points nowhere near the cause."""
    from chessmark.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CLERK_JWKS_URL", "")
    monkeypatch.setenv("CLERK_ISSUER", "")

    with pytest.raises(InsecureConfigurationError, match="CLERK_JWKS_URL"):
        create_app()

    get_settings.cache_clear()


PADDLE = {
    "paddle_webhook_secret": "pdl_ntfset_x",
    "paddle_api_key": "pdl_sdbx_apikey_x",
    "paddle_product_id": "pro_x",
}


@pytest.mark.parametrize("missing", sorted(PADDLE))
def test_selling_credit_half_configured_is_reported(missing: str) -> None:
    """Any one of Paddle's settings without the others takes money it cannot credit, or offers a
    checkout that cannot open. None of them is fine — selling is simply off (ADR-0055, ADR-0056)."""
    problems = Settings(
        **{**SAFE, **PADDLE, missing: "", "openrouter_management_key": "mgmt"}
    ).production_problems()
    assert any("must be set together" in problem for problem in problems), problems


def test_selling_credit_needs_the_key_that_measures_headroom() -> None:
    problems = Settings(**{**SAFE, **PADDLE, "openrouter_management_key": ""}).production_problems()
    assert any("OPENROUTER_MANAGEMENT_KEY" in problem for problem in problems), problems


def test_selling_credit_fully_configured_or_off_is_not_a_problem() -> None:
    assert Settings(**SAFE).production_problems() == []
    configured = Settings(**{**SAFE, **PADDLE, "openrouter_management_key": "mgmt"})
    assert configured.production_problems() == []
