"""Factory: pick a user-store implementation based on ``Settings``.

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

from token_saver.auth.repository import InMemoryUserStore, MongoUserStore, UserStore
from token_saver.config import Settings

__all__ = ["build_user_store", "UnknownUserStoreBackendError"]


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
