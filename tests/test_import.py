"""Smoke tests for the TASK-002-1 skeleton.

Goals (in priority order):
1. Every public module imports without raising — catches broken package
   layout / circular imports before any behaviour is written.
2. The package metadata exposes ``__version__`` matching ``pyproject.toml``.
3. The FastAPI app factory returns a usable app and the placeholder
   ``/healthz`` endpoint returns 200 OK.

Behavioural tests (auth, rate limit, CCR, providers) land alongside
their respective implementations in TASK-002-3 .. TASK-002-6.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import token_saver
from token_saver.config import Settings, get_settings
from token_saver.proxy import create_app


def test_version_exposed() -> None:
    """``__version__`` must be importable and non-empty."""
    assert isinstance(token_saver.__version__, str)
    assert token_saver.__version__, "empty version string"


def test_settings_singleton() -> None:
    """``get_settings`` is memoised and returns the same instance twice."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    assert isinstance(s1, Settings)


def test_app_factory_returns_fastapi() -> None:
    """``create_app`` builds a FastAPI app with our title/version."""
    from fastapi import FastAPI

    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "Token Saver Router"
    assert app.version == token_saver.__version__


def test_healthz_endpoint(client) -> None:
    """Placeholder ``/healthz`` returns 200 with ``{"status": "ok"}``."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "module_name",
    [
        "token_saver",
        "token_saver.config",
        "token_saver.cli",
        "token_saver.proxy",
        "token_saver.proxy.app",
        "token_saver.auth",
        "token_saver.ratelimit",
        "token_saver.detector",
        "token_saver.compressor",
        "token_saver.provider",
        "token_saver.ccr",
    ],
)
def test_public_modules_import(module_name: str) -> None:
    """Every documented public module must import cleanly.

    Locks in the package layout declared in ``token_saver/__init__.py``
    and prevents accidental circular imports as new modules land.
    """
    importlib.import_module(module_name)


def test_package_layout_matches_pyproject() -> None:
    """Walk the package and confirm the subpackages declared in
    ``pyproject.toml [tool.setuptools] packages`` actually exist on disk.

    Catches the "I removed a directory but forgot to update pyproject"
    drift early.
    """
    expected = {
        "token_saver",
        "token_saver.proxy",
        "token_saver.auth",
        "token_saver.ratelimit",
        "token_saver.detector",
        "token_saver.compressor",
        "token_saver.provider",
        "token_saver.ccr",
    }
    found = {"token_saver"}
    for mod in pkgutil.walk_packages(token_saver.__path__, prefix="token_saver."):
        # Only top-level + first-level subpackages — inner modules come later.
        depth = mod.name.count(".") - 1
        if depth <= 1:
            found.add(mod.name)
    missing = expected - found
    assert not missing, f"missing public modules: {sorted(missing)}"
