"""Tests for :class:`RedisSessionStore` and the in-memory fallback.

``fakeredis`` gives us a real Redis wire surface in-process so we
exercise the JSON encode/decode, TTL setting, and lazy-eviction
behaviour without needing a live Redis. We also cover the
``InMemorySessionStore`` regression so a future cycle doesn't
silently regress the in-memory path.
"""

from __future__ import annotations

import time

import fakeredis.aioredis
import pytest

from token_saver.auth.tokens import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionPayload,
    SessionStore,
)

# ----- Redis-backed store -----


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture
async def redis_store(redis_client: fakeredis.aioredis.FakeRedis) -> RedisSessionStore:
    return RedisSessionStore(client=redis_client, ttl_seconds=60)


async def test_set_then_get_round_trip(redis_store: RedisSessionStore) -> None:
    """The store round-trips a SessionPayload through Redis."""
    expires = time.time() + 60
    payload = SessionPayload(user_id="user_alice", role="admin", expires_at=expires)
    await redis_store.set("tok-123", payload)
    found = await redis_store.get("tok-123")
    assert found is not None
    assert found.user_id == "user_alice"
    assert found.role == "admin"
    assert found.expires_at == pytest.approx(expires, abs=0.5)


async def test_get_returns_none_for_missing_token(redis_store: RedisSessionStore) -> None:
    """Unknown token → ``None`` (not an exception)."""
    assert await redis_store.get("never-issued") is None


async def test_revoke_removes_session(redis_store: RedisSessionStore) -> None:
    payload = SessionPayload(
        user_id="user_alice", role="user", expires_at=time.time() + 60
    )
    await redis_store.set("tok-abc", payload)
    assert await redis_store.get("tok-abc") is not None
    await redis_store.revoke("tok-abc")
    assert await redis_store.get("tok-abc") is None


async def test_revoke_missing_token_is_noop(redis_store: RedisSessionStore) -> None:
    """Revoking a token that was never set doesn't raise."""
    await redis_store.revoke("never-issued")


async def test_set_uses_redis_ttl(redis_store: RedisSessionStore, redis_client) -> None:
    """``SET ... EX`` is honoured so Redis itself evicts expired keys."""
    payload = SessionPayload(
        user_id="user_alice", role="user", expires_at=time.time() + 60
    )
    await redis_store.set("tok-ttl", payload)
    ttl = await redis_client.ttl("session:tok-ttl")
    assert ttl > 0
    assert ttl <= 60


async def test_aclose_closes_underlying_client() -> None:
    """``aclose`` shuts down the Redis client cleanly."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(client=client, ttl_seconds=60)
    await store.aclose()
    # Subsequent operations fail because the client is closed —
    # we don't run any here; the absence of an exception during
    # ``aclose`` is the assertion.


# ----- In-memory fallback -----


async def test_in_memory_store_round_trip() -> None:
    store: SessionStore = InMemorySessionStore()
    expires = time.time() + 60
    payload = SessionPayload(user_id="user_alice", role="user", expires_at=expires)
    await store.set("tok", payload)
    found = await store.get("tok")
    assert found is not None
    assert found.user_id == "user_alice"


async def test_in_memory_store_lazily_evicts_expired() -> None:
    """Expired sessions are removed on read, not on a background timer."""
    store: SessionStore = InMemorySessionStore()
    payload = SessionPayload(
        user_id="user_alice", role="user", expires_at=time.time() - 1
    )
    await store.set("tok", payload)
    assert await store.get("tok") is None


async def test_in_memory_store_revoke() -> None:
    store: SessionStore = InMemorySessionStore()
    payload = SessionPayload(
        user_id="user_alice", role="user", expires_at=time.time() + 60
    )
    await store.set("tok", payload)
    await store.revoke("tok")
    assert await store.get("tok") is None
