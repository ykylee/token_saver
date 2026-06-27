"""Provider models catalog cache — Redis-backed hot layer.

Architecture §4.1 calls for ``provider_models:{provider_id}`` with a
1h TTL. The cache is a write-through layer: every successful
``provider.list_models()`` writes the catalog here, and the chat
router reads it before deciding to fall through to a live ``GET
/v1/models`` call.

The cache is intentionally small — a tuple of ``{"id","owned_by"}``
dicts per provider. That's all ``/v1/models`` needs to surface and
all the chat router needs to validate a model id. Anything heavier
(context window, pricing) lands in a follow-up if/when the client
needs it.

Three implementations:

- :class:`RedisModelCatalogCache` — production. Talks to a real
  ``redis.asyncio.Redis`` instance.
- :class:`NullModelCatalogCache` — fallback used in tests and when
  ``Settings.redis_url`` is the empty string (some CI runners don't
  have a Redis handy).
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from redis.asyncio import Redis

__all__ = [
    "ModelCatalogCache",
    "RedisModelCatalogCache",
    "NullModelCatalogCache",
]


@runtime_checkable
class ModelCatalogCache(Protocol):
    """Surface every model catalog cache must implement.

    The cache is *best-effort* — failures during get/set are
    swallowed and treated as a cache miss. The chat router's hot
    path must never 5xx because Redis is down; the cache is an
    optimisation, not a source of truth.
    """

    async def get(self, provider_id: str) -> list[dict[str, Any]] | None: ...
    async def set(
        self, provider_id: str, models: list[dict[str, Any]], *, ttl_seconds: int
    ) -> None: ...
    async def invalidate(self, provider_id: str) -> None: ...
    async def aclose(self) -> None: ...


class NullModelCatalogCache:
    """A no-op cache used in tests and the ``memory`` backend.

    Every call is a miss / no-op. The ``aclose`` shutdown hook is a
    no-op too, so the lifespan can call it unconditionally.
    """

    async def get(self, provider_id: str) -> list[dict[str, Any]] | None:
        return None

    async def set(
        self, provider_id: str, models: list[dict[str, Any]], *, ttl_seconds: int
    ) -> None:
        return None

    async def invalidate(self, provider_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisModelCatalogCache:
    """Redis-backed model catalog cache.

    Keys are namespaced as ``provider_models:{provider_id}`` to
    match the architecture spec. Values are JSON-serialised lists of
    ``{"id","owned_by"}`` dicts — small enough that JSON is cheaper
    than msgpack and human-readable when an operator does a
    ``redis-cli GET`` for debugging.

    All errors are swallowed and translated into cache misses.
    The chat router's hot path must never 5xx because Redis is
    unavailable; the source of truth is Mongo's ``models_cache``
    sub-document.
    """

    def __init__(self, client: Redis, *, key_prefix: str = "provider_models:") -> None:
        self._client = client
        self._prefix = key_prefix

    def _key(self, provider_id: str) -> str:
        return f"{self._prefix}{provider_id}"

    async def get(self, provider_id: str) -> list[dict[str, Any]] | None:
        try:
            raw = await self._client.get(self._key(provider_id))
        except Exception:  # noqa: BLE001 - cache miss on any error
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt entry — treat as a miss; the next set will
            # overwrite.
            return None
        if not isinstance(data, list):
            return None
        return [m for m in data if isinstance(m, dict)]

    async def set(
        self, provider_id: str, models: list[dict[str, Any]], *, ttl_seconds: int
    ) -> None:
        try:
            await self._client.set(
                self._key(provider_id),
                json.dumps(models, separators=(",", ":")),
                ex=ttl_seconds,
            )
        except Exception:  # noqa: BLE001 - cache write is best-effort
            return None

    async def invalidate(self, provider_id: str) -> None:
        try:
            await self._client.delete(self._key(provider_id))
        except Exception:  # noqa: BLE001
            return None

    async def aclose(self) -> None:
        """Close the underlying Redis client.

        Delegates to the client's ``aclose`` — the caller (factory)
        passes a dedicated client per store so shutdown is clean.
        """
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            return None
