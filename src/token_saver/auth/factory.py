"""Factory: pick a user-store + session-store implementation based on ``Settings``.

Single decision point for the in-memory vs. Mongo choice. Centralised
so call sites (``create_app`` and tests) just say
``build_user_store(settings)`` and never grow a switch statement of
their own.

Adding a new backend means: implement the :class:`UserStore` Protocol,
register it in ``_BUILDERS`` below, and update
``Settings.user_store_backend``'s ``Literal`` so the operator's env
file flags a typo at startup.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from token_saver.auth.repository import InMemoryUserStore, MongoUserStore, UserStore
from token_saver.auth.tokens import InMemorySessionStore, RedisSessionStore, SessionStore
from token_saver.config import Settings

__all__ = [
    "build_user_store",
    "build_session_store",
    "UnknownUserStoreBackendError",
]


class UnknownUserStoreBackendError(ValueError):
    """Raised when ``Settings.user_store_backend`` is not in the registry.

    Catching this at startup is intentional — operators get a clear
    error message instead of a generic ``KeyError`` from the switch.
    """


def build_user_store(settings: Settings) -> tuple[UserStore, AsyncIOMotorClient | None]:
    """Return ``(store, optional mongo client)``.

    The mongo client is returned alongside the store so the caller
    can own its lifecycle (``app.state.mongo_client = client``) and
    close it cleanly on shutdown. ``InMemoryUserStore`` returns
    ``None`` — nothing to close.
    """
    backend = settings.user_store_backend
    if backend == "memory":
        return InMemoryUserStore(settings), None
    if backend == "mongo":
        client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongo_url)
        store = MongoUserStore(client=client, db_name=settings.mongo_db_name)
        return store, client
    raise UnknownUserStoreBackendError(
        f"Unknown user_store_backend={backend!r}. "
        "Expected one of: 'memory', 'mongo'."
    )


def build_session_store(
    settings: Settings, redis_client: Redis | None
) -> SessionStore:
    """Pick a session-store backend.

    Returns a :class:`RedisSessionStore` when ``redis_client`` is
    provided (production), :class:`InMemorySessionStore` otherwise.
    The TTL on the Redis variant is taken from
    ``Settings.session_ttl_seconds`` (architecture §4.1 default 1h).
    """
    if redis_client is None:
        return InMemorySessionStore()
    return RedisSessionStore(
        client=redis_client,
        ttl_seconds=settings.session_ttl_seconds,
    )
