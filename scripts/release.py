"""The version on this commit, and its release notes from `CHANGELOG.md`.

Used by `.github/workflows/release.yml`, which tags and publishes a GitHub release whenever `main`
carries a version that has none yet, and by `tests/docs/test_release.py`, which refuses a version
bump that would leave the workflow nothing to publish.

    release.py version               # the version, after checking every place that states it agrees
    release.py notes 0.9.1           # that version's section of CHANGELOG.md, ready for a release
    release.py notes 0.9.1 --repo ahmedsaed/chessmark

**Releases were cut by hand, and the hand forgot.** 0.9.0 and 0.9.1 both shipped to `main` with
their changelog sections written and no tag or release behind either, so the newest release on
GitHub said 0.8.0 for a week. A workflow cannot forget, but it can only publish what is written
down — which is why the same checks run before the merge, not only after it.

Standard library only: the workflow runs this before any dependency is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"
PACKAGE_JSON = REPO_ROOT / "apps" / "web" / "package.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

#: `## [0.9.1] — 2026-10-18`. The date is required: a section without one has not been released.
_SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\](?P<rest>.*)$")
#: A reference-style link definition: `[ADR-0032]: docs/adr/0032-….md`.
_DEFINITION = re.compile(r"^\[(?P<label>[^\]]+)\]:\s*(?P<target>\S+)", re.MULTILINE)
#: An inline link whose target is a path in this repository rather than a URL or an anchor.
_RELATIVE = re.compile(r"\]\((?!https?://|mailto:|#)(?P<path>[^)\s]+)\)")


class ReleaseError(ValueError):
    """What is wrong, written for whoever is about to bump the version."""


def stated_versions() -> dict[str, str]:
    """Every place the version is written, by file."""
    api = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    web = json.loads(PACKAGE_JSON.read_text())["version"]
    return {"apps/api/pyproject.toml": api, "apps/web/package.json": web}


def current_version() -> str:
    """The version, refusing two halves that disagree.

    The API reports one in `/health` and the web image is built from the other. A release named for
    one of them while the other says something else is a release nobody can match to what is
    running.
    """
    versions = stated_versions()
    if len(set(versions.values())) != 1:
        stated = ", ".join(f"{where} says {version}" for where, version in versions.items())
        raise ReleaseError(f"The version is not the same everywhere: {stated}.")
    return next(iter(versions.values()))


def section(changelog: str, version: str) -> str:
    """The body of `version`'s section, without its heading. Raises if there is none."""
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = _SECTION.match(line)
        if match is None:
            continue
        if start is not None:
            return "\n".join(lines[start:index]).strip()
        if match["version"] == version:
            if not match["rest"].strip():
                raise ReleaseError(
                    f"CHANGELOG.md has a [{version}] section with no date. Date it — "
                    f"`## [{version}] — YYYY-MM-DD` — when it is released."
                )
            start = index + 1
    if start is None:
        raise ReleaseError(
            f"CHANGELOG.md has no section for {version}. Move what is under [Unreleased] into "
            f"`## [{version}] — YYYY-MM-DD` in the same change that bumps the version."
        )
    return "\n".join(lines[start:]).strip()


def notes(changelog: str, version: str, repo: str | None = None) -> str:
    """The section as a GitHub release can show it.

    Two things in the file only work *in* the file, and both break silently on a release page: a
    reference-style link whose definition is at the foot of the changelog, and a relative link to
    `docs/…`, which a release resolves against the wrong base. The first is given its definition;
    the second, when the repository is known, is pointed at this tag's copy of the file.
    """
    body = section(changelog, version)

    defined_here = {match["label"] for match in _DEFINITION.finditer(body)}
    used = [
        (match["label"], match["target"])
        for match in _DEFINITION.finditer(changelog)
        if f"[{match['label']}]" in body and match["label"] not in defined_here
    ]
    if used:
        body += "\n\n" + "\n".join(f"[{label}]: {target}" for label, target in used)

    if repo:
        base = f"https://github.com/{repo}/blob/v{version}/"
        body = _RELATIVE.sub(lambda m: f"]({base}{m['path']})", body)
        body = _DEFINITION.sub(
            lambda m: (
                m.group(0)
                if re.match(r"https?://|mailto:|#", m["target"])
                else f"[{m['label']}]: {base}{m['target']}"
            ),
            body,
        )

    return (
        body
        + "\n\n---\n\n"
        + "Published, not deployed: production changes when `./chessmark deploy` is run on the "
        + f"server. To run exactly this release: `./chessmark deploy v{version}`."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="the version, checked to agree everywhere it is written")
    notes_parser = sub.add_parser("notes", help="a version's changelog section, as release notes")
    notes_parser.add_argument("version")
    notes_parser.add_argument("--repo", help="owner/name, to make relative links absolute")
    args = parser.parse_args()

    try:
        if args.command == "version":
            print(current_version())
        else:
            print(notes(CHANGELOG.read_text(), args.version, args.repo))
    except ReleaseError as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
