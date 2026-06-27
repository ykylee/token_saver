"""Provider factory tests — mirror the user-store factory tests.

The factory is the only place that knows about backend names, so we
cover the matrix here:

- ``memory`` → ``InMemoryProviderStore``, ``None`` client.
- ``mongo`` → ``MongoProviderStore``, ``AsyncIOMotorClient`` attached.
- unknown backend → :class:`UnknownProviderStoreBackendError`.
"""

from __future__ import annotations

import base64

import pytest

from token_saver.auth.crypto import derive_master_key
from token_saver.config import Settings
from token_saver.provider.factory import (
    UnknownProviderStoreBackendError,
    build_provider_store,
)
from token_saver.provider.store import InMemoryProviderStore, MongoProviderStore

_MASTER = derive_master_key(base64.b64encode(b"\x42" * 32).decode("ascii"))


def _settings(backend: str) -> Settings:
    return Settings(
        admin_email=None,
        admin_password=None,
        provider_store_backend=backend,  # type: ignore[arg-type]
    )


def test_memory_backend_returns_in_memory_store() -> None:
    """The default backend needs no I/O — no client returned."""
    store, client = build_provider_store(_settings("memory"), master_key=_MASTER)
    assert isinstance(store, InMemoryProviderStore)
    assert client is None


def test_mongo_backend_returns_mongo_store_and_client() -> None:
    """``mongo`` returns both store and motor client for lifecycle ownership."""
    store, client = build_provider_store(_settings("mongo"), master_key=_MASTER)
    assert isinstance(store, MongoProviderStore)
    assert client is not None
    client.close()  # type: ignore[union-attr]


def test_unknown_backend_raises_with_helpful_message() -> None:
    """Typos in env config fail loudly, not silently via KeyError."""
    bad_settings = Settings.model_construct(provider_store_backend="postgres")
    with pytest.raises(UnknownProviderStoreBackendError) as exc_info:
        build_provider_store(bad_settings, master_key=_MASTER)
    assert "postgres" in str(exc_info.value)
    assert "memory" in str(exc_info.value)
    assert "mongo" in str(exc_info.value)


def test_factory_rejects_short_master_key_for_mongo_backend() -> None:
    """The Mongo branch validates the master key at construction time.

    The ``memory`` branch is a no-op (no encryption layer), so the
    validation lives in :class:`MongoProviderStore` rather than the
    factory — covering that here keeps the regression net tight.
    """
    settings = _settings("mongo")
    with pytest.raises(ValueError, match="32 bytes"):
        build_provider_store(settings, master_key=b"short")
