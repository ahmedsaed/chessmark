"""Chessmark: LLM agents playing chess against each other and against humans.

The version is read from the installed distribution rather than written here, so there is exactly
one place it is set — `pyproject.toml`. It used to be a literal in three independently-editable
places (`api/routes/health.py`, `main.py`'s OpenAPI metadata, and the package version), tied
together only by two tests that asserted the string `"0.1.0"`. Those tests could not catch drift:
they had to be edited on every bump, so the failure they produced always read as "the test is out
of date" and never as "the API is reporting a version it is not".
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("chessmark-api")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    # Deliberately not a copy of the real version. A plausible-looking fallback is worse than an
    # obviously wrong one: it would let an uninstalled tree report a number somebody might believe.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
