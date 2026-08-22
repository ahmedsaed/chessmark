#!/usr/bin/env python3
"""Fail if a secret reached the client bundle (AUTH-07).

Invariant 10 says no API key ever reaches the client, and ADR-0011 says Phase 9 proves it by
grepping the built output. This is that check, run in CI on every build.

It looks for two different things, because they fail differently:

1. **Key-shaped strings.** `sk-or-...`, `whsec_...`, a Clerk secret key. These are catastrophic and
   the patterns are specific enough to be worth matching directly.
2. **The actual values from the environment.** A build that inlined a real key would not
   necessarily match a pattern — `NEXT_PUBLIC_` prefixing is a footgun that turns any variable into
   a client-visible one, whatever its name. If a secret is in this process's environment, its
   literal value is searched for too.

Next.js only inlines `NEXT_PUBLIC_*`, so a correctly-named variable is safe by construction. This
check exists for the case where someone renames one to debug something and forgets.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = REPO_ROOT / "apps" / "web" / ".next"

#: Extensions that actually ship to a browser. Server-side chunks under `.next/server` are excluded
#: below — they legitimately hold server config and never reach a client.
CLIENT_SUFFIXES = {".js", ".mjs", ".cjs", ".css", ".html", ".json", ".map", ".txt"}

#: Directories inside `.next` that are server-only.
SERVER_DIRS = {"server", "cache", "trace", "types"}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenRouter API key", re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Clerk secret key", re.compile(r"\bsk_(?:test|live)_[A-Za-z0-9]{16,}")),
    ("Clerk webhook secret", re.compile(r"\bwhsec_[A-Za-z0-9+/=]{16,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{16,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Postgres URL with a password", re.compile(r"postgres(?:ql)?://[^\s:@/\"']+:[^\s@/\"']+@")),
]

#: Environment variables whose *values* must never appear, whatever they look like.
SECRET_ENV_VARS = [
    "OPENROUTER_API_KEY",
    "CLERK_SECRET_KEY",
    "CLERK_WEBHOOK_SECRET",
    "DATABASE_URL",
    "REDIS_URL",
]

#: Too short to be a meaningful search term — matching on these would flag every build.
MIN_SECRET_LENGTH = 12


def client_files(target: Path) -> list[Path]:
    files: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in CLIENT_SUFFIXES:
            continue
        relative = path.relative_to(target)
        if relative.parts and relative.parts[0] in SERVER_DIRS:
            continue
        files.append(path)
    return files


def scan(target: Path) -> list[str]:
    findings: list[str] = []

    env_secrets = [
        (name, value)
        for name in SECRET_ENV_VARS
        if (value := os.environ.get(name, "")) and len(value) >= MIN_SECRET_LENGTH
    ]

    for path in client_files(target):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as error:  # pragma: no cover - unreadable build artefact
            findings.append(f"could not read {path}: {error}")
            continue

        for label, pattern in PATTERNS:
            if match := pattern.search(text):
                # Never print the secret itself — CI logs are frequently public.
                findings.append(
                    f"{label} found in {path.relative_to(target)} "
                    f"(matched {len(match.group(0))} characters)"
                )

        for name, value in env_secrets:
            if value in text:
                findings.append(f"the value of ${name} appears in {path.relative_to(target)}")

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()

    if not args.target.exists():
        print(f"no build at {args.target} — run `pnpm build` first", file=sys.stderr)
        return 2

    files = client_files(args.target)
    if not files:
        # An empty scan passing would make this check worthless the day the build layout changes.
        print(f"no client files found under {args.target}; refusing to pass", file=sys.stderr)
        return 2

    findings = scan(args.target)

    if findings:
        print(f"SECRETS IN CLIENT BUNDLE ({len(findings)}):", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print(f"scanned {len(files)} client files, no secrets found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
