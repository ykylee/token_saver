"""Unit tests for :func:`auth.factory.build_user_store`.

The factory is the only place that knows about backend names, so we
cover the matrix here:

- ``memory`` → ``InMemoryUserStore``, ``None`` client.
- ``mongo`` → ``MongoUserStore``, ``AsyncIOMotorClient`` attached.
- unknown backend → :class:`UnknownUserStoreBackendError` with a
  helpful message.

We don't talk to a real Mongo here — the ``mongo`` branch only checks
that the right types come back. Behavioural CRUD lives in
``test_mongo_user_store.py`` with mongomock-motor.
"""

from __future__ import annotations

import pytest

from token_saver.auth.factory import (
    UnknownUserStoreBackendError,
    build_user_store,
)
from token_saver.auth.repository import InMemoryUserStore, MongoUserStore
from token_saver.config import Settings


def _settings(backend: str) -> Settings:
    return Settings(
        admin_email=None,  # avoid seeding both branches in one go
        admin_password=None,
        user_store_backend=backend,  # type: ignore[arg-type]
    )


def test_memory_backend_returns_in_memory_store() -> None:
    """The default backend needs no I/O — no client returned."""
    store, client = build_user_store(_settings("memory"))
    assert isinstance(store, InMemoryUserStore)
    assert client is None


def test_mongo_backend_returns_mongo_store_and_client() -> None:
    """``mongo`` returns both store and motor client for lifecycle ownership."""
    store, client = build_user_store(_settings("mongo"))
    assert isinstance(store, MongoUserStore)
    assert client is not None
    # We don't close the client here — pytest fixture scope would be
    # cleaner; this is just a type-and-existence check.
    client.close()  # type: ignore[union-attr]


def test_unknown_backend_raises_with_helpful_message() -> None:
    """Typos in env config fail loudly, not silently via KeyError.

    Uses ``model_construct`` to bypass Pydantic's ``Literal`` check —
    the factory is the runtime catch for unknown backends, so the test
    needs to deliver a value Pydantic would normally reject. The
    production path is still protected: ``Settings(user_store_backend=...)``
    raises ``ValidationError`` for typos before the app boots.
    """
    bad_settings = Settings.model_construct(user_store_backend="postgres")
    with pytest.raises(UnknownUserStoreBackendError) as exc_info:
        build_user_store(bad_settings)
    assert "postgres" in str(exc_info.value)
    assert "memory" in str(exc_info.value)
    assert "mongo" in str(exc_info.value)
