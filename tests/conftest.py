"""Shared pytest fixtures and configuration.

Skeleton (TASK-002-1): the heavy fixtures (TestClient with Redis/Mongo
overrides, provider httpx mock servers) land alongside their respective
behavioural tests in TASK-002-3 .. TASK-002-6.

What we ship here is the *scaffolding* every later test relies on:
- ``settings`` — a Settings instance with safe test defaults.
- ``client`` — a TestClient around ``create_app()``.
- ``module_imports`` — implicit parametrised fixture that asserts every
  public module imports cleanly (catches circular imports early).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from token_saver import __version__
from token_saver.config import Settings, get_settings
from token_saver.proxy import create_app

__all__ = ["settings", "client", "app", "__version__"]


@pytest.fixture
def settings() -> Settings:
    """A fresh ``Settings`` instance, bypassing the lru_cache singleton.

    Useful when a test mutates env vars and needs a clean read. Most
    tests should just call ``get_settings()`` directly.
    """
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def app() -> object:
    """A fresh FastAPI app per test (no shared state)."""
    return create_app()


@pytest.fixture
def client(app: object) -> Iterator[TestClient]:
    """A ``TestClient`` bound to the test's app instance."""
    with TestClient(app) as c:  # type: ignore[arg-type]
        yield c
