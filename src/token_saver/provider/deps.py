"""FastAPI dependencies for the provider subsystem.

Mirror of :mod:`auth.deps`: pull handles off ``app.state`` so tests
can swap implementations by setting different attributes on a fresh
app instance.
"""

from __future__ import annotations

from fastapi import Request

from token_saver.provider.cache import ModelCatalogCache
from token_saver.provider.registry import ProviderRegistry
from token_saver.provider.store import ProviderStore

__all__ = [
    "get_provider_registry",
    "get_provider_store",
    "get_model_cache",
]


async def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the process-wide provider registry.

    Raises ``RuntimeError`` if the app was created without wiring
    one — that's a programmer error and should fail loudly so it
    surfaces at first request, not as a confusing 503.
    """
    registry = getattr(request.app.state, "provider_registry", None)
    if registry is None:
        raise RuntimeError(
            "provider_registry is not initialised on app.state. "
            "Did create_app() skip the provider wiring?"
        )
    return registry  # type: ignore[no-any-return]


async def get_provider_store(request: Request) -> ProviderStore:
    """Return the process-wide provider store.

    Mirrors :func:`token_saver.auth.deps.get_user_store`. The store
    is wired in ``create_app``'s lifespan startup hook.
    """
    store = getattr(request.app.state, "provider_store", None)
    if store is None:
        raise RuntimeError(
            "provider_store is not initialised on app.state. "
            "Did create_app() skip the provider wiring?"
        )
    return store  # type: ignore[no-any-return]


async def get_model_cache(request: Request) -> ModelCatalogCache:
    """Return the process-wide model catalog cache.

    Falls back to :class:`NullModelCatalogCache` when Redis is not
    configured — callers should treat the null impl as "always
    miss" rather than special-casing the absence.
    """
    cache = getattr(request.app.state, "model_cache", None)
    if cache is None:
        raise RuntimeError(
            "model_cache is not initialised on app.state. "
            "Did create_app() skip the provider wiring?"
        )
    return cache  # type: ignore[no-any-return]
