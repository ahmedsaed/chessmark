#!/usr/bin/env python3
"""Check that Clerk is configured correctly, before trusting it with anything.

    make verify-clerk

Phase 9 is a hard gate, and every one of its controls was tested against a locally generated
keypair — the suite never reaches Clerk, by design. This script closes that gap: it makes the real
network calls, against the real instance, and says which parts are wrong.

It cannot verify a real sign-in on its own; that needs a browser and a human. Everything it *can*
check without one, it checks.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.core.config import get_settings  # noqa: E402

OK, BAD, WARN = "\033[38;5;108m✓\033[0m", "\033[38;5;167m✗\033[0m", "\033[38;5;179m!\033[0m"

failures = 0
warnings = 0


def ok(message: str) -> None:
    print(f"  {OK} {message}")


def bad(message: str, fix: str = "") -> None:
    global failures
    failures += 1
    print(f"  {BAD} {message}")
    if fix:
        print(f"      → {fix}")


def warn(message: str, fix: str = "") -> None:
    global warnings
    warnings += 1
    print(f"  {WARN} {message}")
    if fix:
        print(f"      → {fix}")


def fetch(url: str, timeout: int = 15) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def main() -> int:
    settings = get_settings()
    print("\nClerk configuration\n")

    # ---------------------------------------------------------------- keys
    publishable = settings.clerk_publishable_key
    if not publishable:
        bad("CLERK_PUBLISHABLE_KEY is not set", "Dashboard → API keys → Publishable key")
    elif not publishable.startswith(("pk_test_", "pk_live_")):
        bad(
            f"CLERK_PUBLISHABLE_KEY looks wrong ({publishable[:12]}…)",
            "expected pk_test_ or pk_live_",
        )
    else:
        kind = "development" if publishable.startswith("pk_test_") else "PRODUCTION"
        ok(f"publishable key present ({kind} instance)")

    secret = settings.clerk_secret_key
    if not secret:
        warn(
            "CLERK_SECRET_KEY is not set", "not used yet, but needed for Clerk's backend API later"
        )
    elif not secret.startswith(("sk_test_", "sk_live_")):
        bad("CLERK_SECRET_KEY looks wrong", "expected sk_test_ or sk_live_")
    else:
        ok("secret key present")
        if secret.startswith("sk_") and publishable.startswith("pk_live_") != secret.startswith(
            "sk_live_"
        ):
            bad(
                "publishable and secret keys are from different instances",
                "one is test and the other is live — tokens will never verify",
            )

    # ---------------------------------------------------------------- jwks
    jwks_url = settings.clerk_jwks_url
    if not jwks_url:
        bad(
            "CLERK_JWKS_URL is not set — no request could ever authenticate",
            "it is <issuer>/.well-known/jwks.json",
        )
    else:
        try:
            jwks = fetch(jwks_url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            bad(f"CLERK_JWKS_URL is not reachable: {error}", f"check {jwks_url}")
        else:
            keys = jwks.get("keys") if isinstance(jwks, dict) else None
            if not isinstance(keys, list) or not keys:
                bad("JWKS contains no keys", "the URL resolved but is not a JWKS document")
            else:
                algs = {k.get("alg") for k in keys if isinstance(k, dict)}
                ok(f"JWKS reachable, {len(keys)} key(s), alg {', '.join(sorted(map(str, algs)))}")
                if algs - {"RS256"}:
                    warn(
                        f"JWKS advertises {algs}, and we only accept RS256",
                        "not a problem unless Clerk stops signing with RS256",
                    )

    # ---------------------------------------------------------------- issuer
    issuer = settings.clerk_issuer
    if not issuer:
        bad(
            "CLERK_ISSUER is not set — a token from ANY Clerk instance would verify",
            "it is your Frontend API URL, e.g. https://your-app.clerk.accounts.dev",
        )
    elif jwks_url and urlsplit(issuer).netloc != urlsplit(jwks_url).netloc:
        bad(
            f"CLERK_ISSUER host ({urlsplit(issuer).netloc}) does not match "
            f"CLERK_JWKS_URL host ({urlsplit(jwks_url).netloc})",
            "these must be the same instance or every token is rejected",
        )
    else:
        ok(f"issuer set and matches the JWKS host ({urlsplit(issuer).netloc})")

    # ---------------------------------------------------------------- webhook
    webhook = settings.clerk_webhook_secret
    if not webhook:
        warn(
            "CLERK_WEBHOOK_SECRET is not set — the webhook refuses every delivery",
            "Dashboard → Webhooks → your endpoint → Signing Secret. Sign-in still works without it; "
            "only email changes and account deletions would be missed.",
        )
    elif not webhook.startswith("whsec_"):
        bad("CLERK_WEBHOOK_SECRET does not start with whsec_")
    else:
        try:
            base64.b64decode(webhook.removeprefix("whsec_"))
        except Exception:
            bad("CLERK_WEBHOOK_SECRET is not valid base64 after the prefix")
        else:
            ok("webhook signing secret present and decodable")

    # ---------------------------------------------------------------- frontend
    env_path = REPO_ROOT / ".env"
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=" not in env_text or not any(
        line.startswith("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=") and len(line.strip()) > 35
        for line in env_text.splitlines()
    ):
        bad(
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set in .env",
            "the frontend needs its own copy; without it the site stays permanently signed out",
        )
    else:
        ok("frontend publishable key present")

    # ---------------------------------------------------------------- production gate
    problems = settings.production_problems()
    if settings.environment == "production" and problems:
        for problem in problems:
            bad(f"production: {problem}")

    print()
    if failures:
        print(f"{failures} problem(s), {warnings} warning(s) — auth will not work yet.\n")
        return 1

    print(f"Configuration looks correct ({warnings} warning(s)).")
    print("Still unverified: an actual sign-in. Start the app and sign in to confirm.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
