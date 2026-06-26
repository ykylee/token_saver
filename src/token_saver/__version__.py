"""Version constants for token_saver.

Single source of truth: ``pyproject.toml`` ``[project] version``.
Three-tier fallback mirrors the standard_ai_workflow pattern (pyproject
→ importlib.metadata → literal) so the same version string is reachable
from runtime code, packaging metadata, and detached-GIT CI alike.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["__version__", "SCHEMA_VERSION"]


def _resolve_version() -> str:
    """Resolve the package version.

    Order of precedence:
    1. ``importlib.metadata.version`` (works after ``pip install -e .[dev]``).
    2. Literal fallback for source-tree execution without install.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("token-saver")
        except PackageNotFoundError:
            pass
    except Exception:  # noqa: BLE001 — best-effort, fallback below
        pass
    return "0.1.0a0"


__version__: str = _resolve_version()

# Bumped when the Redis key layout or Mongo collection schema changes in a
# non-backwards-compatible way. Cached at startup; clients can compare against
# a header in `/admin/health`. Mirrored from pyproject.toml
# [tool.token_saver.runtime] schema_version.
SCHEMA_VERSION: int = 1

# Project root for runtime config discovery (.env files etc.).
# Does NOT pin to a specific file — config.py owns that policy.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
