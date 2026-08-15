"""`game/` must stay a pure domain.

The chess rules are the one part of Chessmark that can be reasoned about — and tested — with no
knowledge of LLMs, databases, HTTP, or configuration. That is only true for as long as nobody
imports those things, so this test enforces it structurally rather than by convention.

Uses the AST rather than an import-linter dependency: it is exact, and it costs nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import chessmark.game

FORBIDDEN_PACKAGES = ("chessmark.db", "chessmark.agents", "chessmark.api", "chessmark.core")

GAME_PACKAGE = Path(chessmark.game.__file__).parent
GAME_MODULES = sorted(GAME_PACKAGE.glob("*.py"))


def _imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_package_actually_has_modules() -> None:
    """Guard against the test silently passing because the glob found nothing."""
    assert len(GAME_MODULES) >= 4


@pytest.mark.parametrize("module_path", GAME_MODULES, ids=lambda p: p.name)
def test_game_module_imports_nothing_outside_the_domain(module_path: Path) -> None:
    imported = _imported_modules(module_path.read_text(encoding="utf-8"))

    offending = sorted(
        name
        for name in imported
        if any(name == package or name.startswith(f"{package}.") for package in FORBIDDEN_PACKAGES)
    )

    assert not offending, (
        f"{module_path.name} imports {offending}. The chess domain must stay pure — "
        f"move the dependency to the caller instead."
    )


@pytest.mark.parametrize("module_path", GAME_MODULES, ids=lambda p: p.name)
def test_game_module_uses_no_relative_imports(module_path: Path) -> None:
    """Absolute imports only, so the purity check above cannot be sidestepped."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    relative = [
        node.module or "."
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]

    assert not relative, f"{module_path.name} uses relative imports: {relative}"
