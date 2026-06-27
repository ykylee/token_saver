"""Tests for :class:`RedisModelCatalogCache` + :class:`NullModelCatalogCache`.

The cache wraps ``redis.asyncio.Redis`` (here faked via fakeredis)
and translates every operation to a best-effort cache hit. Failures
during get/set/invalidate are swallowed so the chat router's hot
path can never 5xx because Redis is unavailable.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from token_saver.provider.cache import (
    ModelCatalogCache,
    NullModelCatalogCache,
    RedisModelCatalogCache,
)


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture
def redis_cache(redis_client: fakeredis.aioredis.FakeRedis) -> RedisModelCatalogCache:
    return RedisModelCatalogCache(client=redis_client)


async def test_set_then_get_round_trip(redis_cache: RedisModelCatalogCache) -> None:
    """Cached models come back exactly as written."""
    models: list[dict[str, Any]] = [
        {"id": "gpt-4o-mini", "owned_by": "openai"},
        {"id": "gpt-4o", "owned_by": "openai"},
    ]
    await redis_cache.set("provider_x", models, ttl_seconds=60)
    cached = await redis_cache.get("provider_x")
    assert cached == models


async def test_get_returns_none_on_cache_miss(
    redis_cache: RedisModelCatalogCache,
) -> None:
    """Unknown key → ``None`` (caller falls through to Mongo)."""
    assert await redis_cache.get("never-set") is None


async def test_invalidate_removes_entry(redis_cache: RedisModelCatalogCache) -> None:
    """``invalidate`` deletes the key."""
    await redis_cache.set(
        "provider_x",
        [{"id": "gpt-4o-mini", "owned_by": "openai"}],
        ttl_seconds=60,
    )
    assert await redis_cache.get("provider_x") is not None
    await redis_cache.invalidate("provider_x")
    assert await redis_cache.get("provider_x") is None


async def test_invalidate_missing_is_noop(redis_cache: RedisModelCatalogCache) -> None:
    """Invalidating a missing key doesn't raise."""
    await redis_cache.invalidate("never-set")


async def test_set_respects_ttl(
    redis_cache: RedisModelCatalogCache, redis_client: fakeredis.aioredis.FakeRedis
) -> None:
    """TTL is forwarded to Redis ``EX`` so the key auto-evicts."""
    await redis_cache.set(
        "provider_x",
        [{"id": "gpt-4o-mini", "owned_by": "openai"}],
        ttl_seconds=60,
    )
    ttl = await redis_client.ttl("provider_models:provider_x")
    assert ttl > 0
    assert ttl <= 60


async def test_corrupt_entry_returns_none(redis_cache: RedisModelCatalogCache) -> None:
    """A hand-corrupted entry is treated as a miss (next ``set`` overwrites)."""
    await redis_cache._client.set("provider_models:bad", "not-json")  # type: ignore[attr-defined]
    assert await redis_cache.get("bad") is None


# ----- Null fallback -----


async def test_null_cache_always_misses() -> None:
    cache: ModelCatalogCache = NullModelCatalogCache()
    assert await cache.get("provider_x") is None


async def test_null_cache_set_is_noop() -> None:
    """``set`` and ``invalidate`` are no-ops on the null cache."""
    cache = NullModelCatalogCache()
    await cache.set("provider_x", [{"id": "x", "owned_by": "openai"}], ttl_seconds=60)
    await cache.invalidate("provider_x")
    assert await cache.get("provider_x") is None


async def test_null_cache_aclose_is_noop() -> None:
    """The null cache's ``aclose`` is a no-op (no client to close)."""
    cache = NullModelCatalogCache()
    await cache.aclose()
