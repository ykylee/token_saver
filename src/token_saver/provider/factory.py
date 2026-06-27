"""Provider subsystem factory — pick a backend for each piece.

Three things to wire at startup, all keyed off ``Settings``:

1. **Provider store** — in-memory (tests, ``memory`` backend) or
   Mongo (``mongo`` backend). Single decision point, mirrors
   :func:`token_saver.auth.factory.build_user_store`.
2. **Model catalog cache** — Redis-backed in production, no-op
   fallback for tests. Holds the 1h hot cache that keeps the chat
   router off the upstream ``GET /v1/models`` call.
3. **Redis client** — shared across the session store
   (auth/tokens.py) and the model catalog cache. Created once per
   process; the lifespan closes it on shutdown.

Why the Redis client comes back through the factory (rather than
being a module-level singleton): the auth factory needs the same
client for :class:`RedisSessionStore`, and we want a single owner
of the lifecycle.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from token_saver.config import Settings
from token_saver.provider.cache import (
    ModelCatalogCache,
    NullModelCatalogCache,
    RedisModelCatalogCache,
)
from token_saver.provider.store import (
    InMemoryProviderStore,
    MongoProviderStore,
    ProviderStore,
)

__all__ = [
    "build_provider_store",
    "build_model_cache",
    "build_redis_client",
    "UnknownProviderStoreBackendError",
]


class UnknownProviderStoreBackendError(ValueError):
    """Raised when ``Settings.provider_store_backend`` is not in the registry.

    Catching this at startup is intentional — operators get a clear
    error message instead of a generic ``KeyError`` from the switch.
    """


def build_provider_store(
    settings: Settings, *, master_key: bytes
) -> tuple[ProviderStore, AsyncIOMotorClient | None]:
    """Return ``(store, optional mongo client)``.

    Mirrors :func:`token_saver.auth.factory.build_user_store`:
    single decision point for the in-memory vs. Mongo choice. The
    caller (``create_app``) owns the returned mongo client's
    lifecycle so it can ``close()`` on shutdown.
    """
    backend = settings.provider_store_backend
    if backend == "memory":
        return InMemoryProviderStore(), None
    if backend == "mongo":
        client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongo_url)
        store: ProviderStore = MongoProviderStore(
            client=client,
            db_name=settings.mongo_db_name,
            master_key=master_key,
        )
        return store, client
    raise UnknownProviderStoreBackendError(
        f"Unknown provider_store_backend={backend!r}. "
        "Expected one of: 'memory', 'mongo'."
    )


def build_redis_client(settings: Settings) -> Redis | None:
    """Build a Redis client if ``Settings.redis_url`` is configured.

    Returns ``None`` when ``redis_url`` is empty — the caller falls
    back to in-memory / null implementations instead. We avoid a
    default-on Redis connection so tests don't need a live Redis
    (and CI runners without Redis still pass).
    """
    if not settings.redis_url:
        return None
    client: Redis = Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    return client


def build_model_cache(
    settings: Settings, redis_client: Redis | None
) -> ModelCatalogCache:
    """Return a Redis-backed cache when Redis is configured, else a no-op.

    ``Settings.provider_models_cache_ttl_seconds`` controls the TTL
    (architecture §4.1 default 3600s = 1h).
    """
    if redis_client is None:
        return NullModelCatalogCache()
    return RedisModelCatalogCache(client=redis_client)
