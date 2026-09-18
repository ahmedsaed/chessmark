"""The documents are a compatibility surface, and nothing was checking them.

CLAUDE.md sends every reader — person or agent — to `docs/` as the source of truth, and an ADR is
immutable precisely so a decision keeps its reasoning attached. That only works while a citation
resolves to the decision it names. Four kinds of rot had accumulated with nothing able to see them:

* **Two ADRs numbered 0032.** Different decisions, same day, same number, only one indexed. The
  collision reached shipped prose: `CHANGELOG.md` defines one `[ADR-0032]` link reference, so a
  line about the leaderboard's cost sent readers to the context arithmetic. `agents/llm.py` cited
  0032 meaning one and `bench/snapshot.py` meaning the other.
* **Citations left behind by a rename.** `0034` pointed at `0015-endpoint-pinning-and-quantization.md`
  and `0035` at `0025-reasoning-withheld-mid-game.md` — a file that was never written at all, while
  0025 went to an unrelated decision. Following it landed a reader on the wrong rule.
* **An anchor into a section that had been renamed**, which reads as a working link and silently
  drops the reader at the top of the page.
* **Two header formats**, which is how the first audit of the index misparsed it.

These are all mechanical, which is the whole argument for a test rather than a habit: this is
exactly the class of decay that a careful person does not notice and a passing suite never mentions.
It is the same reasoning as `tests/game/test_purity.py` — a structural rule the codebase relies on,
checked by reading the files rather than by remembering.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
ADR_DIR = REPO / "docs" / "adr"

#: Markdown that is ours to keep correct. The virtualenv and `node_modules` vendor thousands of
#: files whose links are somebody else's problem.
VENDORED = ("node_modules", ".venv", ".next", "site-packages", ".lighthouseci", "test-results")

#: An ADR filename: four digits, a hyphen, a kebab-case slug.
ADR_FILE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")

#: `# 0046. Title`, the format 46 of 48 files already used.
ADR_TITLE = re.compile(r"^# (\d{4})\. \S")

#: A file kept only so old links still land. It carries a number another ADR now owns, which is
#: the one case where a duplicate number is correct rather than a mistake.
RENUMBERED = "**Status:** Renumbered"


def adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("*.md") if ADR_FILE.match(p.name))


def repo_markdown() -> list[Path]:
    return sorted(p for p in REPO.rglob("*.md") if not any(part in VENDORED for part in p.parts))


def is_pointer(path: Path) -> bool:
    return RENUMBERED in path.read_text(encoding="utf-8")


def test_no_two_adrs_claim_the_same_number() -> None:
    """The failure that produced this file. A number must name one decision.

    A pointer left behind by a renumber is exempt: it exists *because* it shares the number, and
    it says where the decision went.
    """
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in adr_files():
        if is_pointer(path):
            continue
        number = ADR_FILE.match(path.name).group(1)  # type: ignore[union-attr]
        by_number[number].append(path.name)

    clashes = {n: sorted(f) for n, f in by_number.items() if len(f) > 1}
    assert not clashes, (
        "two ADRs claim the same number, so a citation of it is ambiguous: "
        f"{clashes}. Renumber the later one and leave a pointer at the old path "
        f"('{RENUMBERED}') so existing links still land."
    )


def test_every_adr_is_in_the_index() -> None:
    """An unindexed ADR is one nobody finds. That is how 0032 stayed invisible for a fortnight."""
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    missing = [p.name for p in adr_files() if not is_pointer(p) and f"({p.name})" not in index]

    assert not missing, f"ADRs not listed in docs/adr/README.md: {missing}"


def test_every_index_row_points_at_a_real_adr() -> None:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    named = set(re.findall(r"\((\d{4}-[a-z0-9-]+\.md)\)", index))
    missing = sorted(name for name in named if not (ADR_DIR / name).exists())

    assert not missing, f"docs/adr/README.md links to ADRs that do not exist: {missing}"


@pytest.mark.parametrize("path", adr_files(), ids=lambda p: p.name)
def test_an_adr_states_its_number_and_status_the_same_way_as_every_other(path: Path) -> None:
    """One format, because a reader and a script both have to parse these.

    Two files used `# ADR-0044: Title` and `**Status:** accepted · **Date:** …` on one line. It is
    cosmetic right up until something tries to read the status, at which point it silently reports
    the wrong one — which is what happened the first time this index was audited.
    """
    lines = path.read_text(encoding="utf-8").split("\n")

    title = ADR_TITLE.match(lines[0])
    assert title, f"{path.name}: first line must be '# NNNN. Title', got {lines[0]!r}"
    assert title.group(1) == ADR_FILE.match(path.name).group(1), (  # type: ignore[union-attr]
        f"{path.name}: the number in the title disagrees with the filename"
    )

    status = next((line for line in lines[:8] if line.startswith("**Status:**")), None)
    assert status, f"{path.name}: no '**Status:**' line in the header"
    assert re.fullmatch(
        r"\*\*Status:\*\* (Accepted|Proposed|Superseded|Amended|Renumbered)", status
    ), f"{path.name}: status must be one capitalised word on its own line, got {status!r}"


@pytest.mark.parametrize("path", repo_markdown(), ids=lambda p: str(p))
def test_every_relative_link_in_our_markdown_resolves(path: Path) -> None:
    """A link to a renamed file is worse than no link: it reads as an answer.

    Anchors are checked too. `ADR-0046` pointed into a FRONTEND.md section that the same change had
    renamed — a link that looks live and drops the reader at the top of the page instead.
    """
    text = without_code(path.read_text(encoding="utf-8"))
    broken: list[str] = []

    for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part, _, anchor = target.partition("#")
        if not file_part:
            continue

        dest = (path.parent / file_part).resolve()
        if not dest.exists():
            broken.append(f"{target} (no such file)")
        elif anchor and dest.suffix == ".md" and anchor not in headings(dest):
            broken.append(f"{target} (no such heading)")

    assert not broken, f"{path.relative_to(REPO)} has dead links: {broken}"


def without_code(text: str) -> str:
    """Markdown with fenced blocks removed.

    A link inside a ``` block is an *example* of a link — `docs/adr/README.md` shows the ADR
    template with `Superseded by [NNNN](...)` in it, and a checker that cannot tell the two apart
    reports the documentation of the format as a violation of it.
    """
    return re.sub(r"^```.*?^```", "", text, flags=re.DOTALL | re.MULTILINE)


def headings(path: Path) -> set[str]:
    """GitHub's anchor slugs for one document: lowercased, punctuation dropped, spaces hyphenated."""
    found = set()
    for line in path.read_text(encoding="utf-8").split("\n"):
        heading = re.match(r"#+\s+(.*)", line)
        if heading:
            slug = re.sub(r"[^\w\s-]", "", heading.group(1).strip().lower())
            found.add(re.sub(r"\s+", "-", slug))
    return found
