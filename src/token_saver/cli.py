"""CLI entry point: ``token-saver`` console script.

Subcommands (TASK-002-7):
- ``serve`` — run the FastAPI app via uvicorn.
- ``provider test`` — invoke ``BaseProvider.test_connection`` ad-hoc.
- ``provider add`` — register a provider (test → list_models → Mongo insert).
- ``provider list`` — enumerate registered providers.
- ``provider refresh`` — re-fetch models for one or all providers.
- ``provider delete`` — remove a registered provider.

TASK-002-1 skeleton: the entry point exists but the subcommand surface
is intentionally empty (raises ``NotImplementedError``).
"""

from __future__ import annotations

import sys

__all__ = ["main"]


def main() -> int:
    """Console script entry — wired up in pyproject.toml [project.scripts].

    Stub for TASK-002-1. TASK-002-7 will replace this with the Click
    group dispatcher.
    """
    sys.stderr.write(
        "token-saver: skeleton build (TASK-002-1). "
        "Subcommand surface ships in TASK-002-7.\n"
    )
    return 2
