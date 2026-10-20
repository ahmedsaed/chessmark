"""A version bump is checked before it merges, because after it is too late.

`.github/workflows/release.yml` publishes a release for any version on `main` that has none, using
that version's section of CHANGELOG.md. It runs *after* the merge, so everything it needs has to be
true *before* it: the version stated the same way in both halves, and a dated changelog section for
it. These run in `make check` and in CI's API job, which is what turns "the release workflow failed
on main" into "this pull request cannot merge yet".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]

_spec = importlib.util.spec_from_file_location("release", REPO / "scripts" / "release.py")
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
sys.modules["release"] = release
_spec.loader.exec_module(release)


def test_both_halves_state_the_same_version() -> None:
    versions = release.stated_versions()
    assert len(set(versions.values())) == 1, f"the version disagrees with itself: {versions}"


def test_the_current_version_has_a_dated_changelog_section() -> None:
    """The release workflow publishes this section. Without it, the merge would release nothing."""
    version = release.current_version()

    body = release.section((REPO / "CHANGELOG.md").read_text(), version)

    assert body, f"CHANGELOG.md's [{version}] section is empty"


CHANGELOG = """# Changelog

## [Unreleased]

- Not yet.

## [0.2.0] — 2026-10-20

**Headline.** See [the decision](docs/adr/0070-x.md) and [ADR-0032].

### Fixed

- A thing, [upstream](https://example.com/issue).

## [0.1.0] — 2026-10-01

- The first.

## [0.0.9]

[ADR-0032]: docs/adr/0032-y.md
[unused]: https://example.com/unused
"""


def test_a_section_runs_to_the_next_heading_and_no_further() -> None:
    body = release.section(CHANGELOG, "0.2.0")

    assert body.startswith("**Headline.**")
    assert body.endswith("[upstream](https://example.com/issue).")
    assert "The first" not in body


def test_notes_carry_the_link_definitions_a_section_uses_and_no_others() -> None:
    """A reference link's definition sits at the foot of the file; a release shows only the section."""
    notes = release.notes(CHANGELOG, "0.2.0")

    assert "[ADR-0032]: docs/adr/0032-y.md" in notes
    assert "[unused]" not in notes


def test_relative_links_point_at_this_releases_copy_of_the_file() -> None:
    """On a release page `docs/…` resolves against the wrong base and 404s."""
    notes = release.notes(CHANGELOG, "0.2.0", repo="owner/name")

    base = "https://github.com/owner/name/blob/v0.2.0/"
    assert f"[the decision]({base}docs/adr/0070-x.md)" in notes
    assert f"[ADR-0032]: {base}docs/adr/0032-y.md" in notes
    assert "[upstream](https://example.com/issue)" in notes


def test_notes_say_a_release_is_not_a_deploy() -> None:
    """Merging is not deploying (CLAUDE.md), and a release page is easy to read as "this is live"."""
    assert "./chessmark deploy v0.2.0" in release.notes(CHANGELOG, "0.2.0")


def test_a_version_with_no_section_is_refused() -> None:
    with pytest.raises(release.ReleaseError, match=r"no section for 0\.3\.0"):
        release.section(CHANGELOG, "0.3.0")


def test_an_undated_section_is_refused() -> None:
    """An undated heading is a section somebody started and has not released."""
    with pytest.raises(release.ReleaseError, match="no date"):
        release.section(CHANGELOG, "0.0.9")
