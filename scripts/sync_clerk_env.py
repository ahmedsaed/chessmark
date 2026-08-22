#!/usr/bin/env python3
"""Reconcile Clerk's frontend keys into the root `.env` the backend reads.

    make sync-clerk-env

`clerk init` writes `apps/web/.env.local`, which is right for Next.js and invisible to everything
else. This project is a monorepo: the Python API reads the **root** `.env`, and it needs two values
the Clerk CLI does not write anywhere — `CLERK_JWKS_URL` and `CLERK_ISSUER`.

Both are derivable. A publishable key is `pk_<env>_<base64url of "frontend-api-host$">`, so the
instance host, the issuer, and the JWKS URL all fall out of a value that is public by design.

Edits in place, key by key. It never rewrites the file wholesale, because the root `.env` also
holds the OpenRouter key and everything else this project needs, and `clerk env pull --file .env`
would have taken the lot with it. Values are never printed.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = REPO_ROOT / ".env"
WEB_ENV = REPO_ROOT / "apps" / "web" / ".env.local"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def frontend_api_host(publishable_key: str) -> str | None:
    """Decode the instance host out of a publishable key.

    `pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk` → `example.clerk.accounts.dev`. The trailing
    `$` is a delimiter Clerk appends before encoding.
    """
    for prefix in ("pk_test_", "pk_live_"):
        if publishable_key.startswith(prefix):
            encoded = publishable_key[len(prefix) :]
            padded = encoded + "=" * (-len(encoded) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None
            return decoded.rstrip("$").strip() or None
    return None


def set_key(text: str, key: str, value: str) -> tuple[str, bool]:
    """Replace `key=...` in place, or append it. Returns the text and whether anything changed."""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"

    if pattern.search(text):
        updated = pattern.sub(replacement, text, count=1)
        return updated, updated != text

    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}{replacement}\n", True


def main() -> int:
    web = read_env(WEB_ENV)
    if not web:
        print(f"no {WEB_ENV.relative_to(REPO_ROOT)} — run `clerk init` first", file=sys.stderr)
        return 2

    publishable = web.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
    secret = web.get("CLERK_SECRET_KEY", "")

    if not publishable:
        print("no publishable key in the web env file", file=sys.stderr)
        return 2

    host = frontend_api_host(publishable)
    if not host:
        print("could not decode the instance host from the publishable key", file=sys.stderr)
        return 2

    wanted = {
        "CLERK_PUBLISHABLE_KEY": publishable,
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": publishable,
        "CLERK_ISSUER": f"https://{host}",
        "CLERK_JWKS_URL": f"https://{host}/.well-known/jwks.json",
    }
    if secret:
        wanted["CLERK_SECRET_KEY"] = secret

    text = ROOT_ENV.read_text(encoding="utf-8") if ROOT_ENV.exists() else ""
    changed: list[str] = []
    for key, value in wanted.items():
        text, did = set_key(text, key, value)
        if did:
            changed.append(key)

    ROOT_ENV.write_text(text, encoding="utf-8")

    print(f"instance: {host}")
    print(f"updated {len(changed)} key(s) in .env: {', '.join(changed) or 'none'}")
    print("run `make verify-clerk` to check it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
