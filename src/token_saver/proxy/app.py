"""FastAPI application factory.

TASK-002-4 wired :func:`auth.factory.build_user_store` into the boot
path. TASK-002-5-b adds the provider subsystem on top:

- :func:`provider.factory.build_provider_store` — picks
  ``InMemoryProviderStore`` or ``MongoProviderStore`` based on
  ``Settings.provider_store_backend``.
- :func:`provider.factory.build_model_cache` — picks a Redis cache
  or a no-op fallback based on whether ``Settings.redis_url`` is set.
- :func:`auth.factory.build_session_store` — same Redis/no-Redis
  decision for the bearer-token session store.

The FastAPI lifespan:

1. **startup** — runs ``MongoUserStore.ensure_indexes`` and
   ``MongoProviderStore.ensure_indexes``, then idempotently seeds
   the admin via :func:`auth.seed`. No-op for the in-memory
   backends.
2. **shutdown** — closes the Redis client, the mongo client
   (user + provider stores share one), and the in-process
   provider registry's HTTP clients.

In-memory mode keeps the old zero-config behaviour for tests and
local development; production / docker-compose flip
``TOKEN_SAVER_USER_STORE_BACKEND=mongo`` (and optionally
``TOKEN_SAVER_PROVIDER_STORE_BACKEND=mongo``) and the same factory
code brings up the real collections.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from token_saver import __version__
from token_saver.auth.crypto import derive_master_key
from token_saver.auth.factory import build_session_store, build_user_store
from token_saver.auth.repository import MongoUserStore, UserStore
from token_saver.auth.seed import SeedAdminSkipped, seed_admin
from token_saver.auth.tokens import SessionStore
from token_saver.config import Settings, get_settings
from token_saver.provider.factory import build_model_cache, build_provider_store, build_redis_client
from token_saver.provider.registry import ProviderRegistry
from token_saver.provider.store import MongoProviderStore, ProviderStore
from token_saver.proxy.routes import admin, auth, chat_completions, models, providers

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(app: FastAPI, settings: Settings) -> AsyncIterator[None]:
    """Startup/shutdown hooks for the FastAPI app.

    Kept as a free function so ``create_app`` stays small and tests
    can reason about it in isolation. ``SeedAdminSkipped`` is the one
    exception we intentionally swallow — it signals "admin already
    exists", not a startup failure. Anything else propagates.
    """
    user_store: UserStore = app.state.user_store
    if isinstance(user_store, MongoUserStore):
        await user_store.ensure_indexes()

    provider_store: ProviderStore = app.state.provider_store
    if isinstance(provider_store, MongoProviderStore):
        await provider_store.ensure_indexes()

    try:
        await seed_admin(settings, user_store)
    except SeedAdminSkipped:
        pass

    yield

    # Shutdown — close in reverse construction order. Redis first
    # (faster cleanup), then Mongo (slower socket pool drain), then
    # the in-process provider registry (closes httpx clients).
    redis_client: Redis | None = getattr(app.state, "redis_client", None)
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            pass

    mongo_client: AsyncIOMotorClient | None = app.state.mongo_client
    if mongo_client is not None:
        mongo_client.close()

    provider_registry: ProviderRegistry | None = getattr(
        app.state, "provider_registry", None
    )
    if provider_registry is not None:
        await provider_registry.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured ``FastAPI`` instance.

    Lifecycle context (session/user/provider stores, Mongo client,
    Redis client) attaches via ``app.state`` so request handlers can
    grab it via ``request.app.state.<attr>`` without going through
    dependency injection machinery.

    Behaviour lands incrementally:
    - TASK-002-2: mock ``/v1/chat/completions``, ``/v1/models``, ``/admin/health``.
    - TASK-002-3: ``/v1/auth/login`` + RBAC dependencies + ``/admin/health`` admin-only.
    - TASK-002-4: Mongo user-store backend + admin seed + indexes
      via FastAPI lifespan.
    - TASK-002-5-a: provider router + Provider Registry + real forward.
    - TASK-002-5-b (current): provider store + Mongo providers +
      AES-GCM encryption + Redis cache + Redis session +
      CRUD routes + per-user lookup.
    - TASK-002-6: end-to-end fixture regression.
    - TASK-002-7: full CLI surface (``token-saver serve`` …).
    """
    resolved = settings or get_settings()
    master_key = derive_master_key(resolved.master_key)

    # Build the user store up-front so we can reference it inside the
    # lifespan closure. ``build_user_store`` returns the optional mongo
    # client so we can close it on shutdown.
    user_store, mongo_client = build_user_store(resolved)

    # Provider subsystem — picks store + cache backends. The redis
    # client is built once and shared with the session store.
    provider_store, provider_mongo_client = build_provider_store(
        resolved, master_key=master_key
    )
    redis_client: Redis | None = build_redis_client(resolved)
    model_cache = build_model_cache(resolved, redis_client)

    app = FastAPI(
        title="Token Saver Router",
        version=__version__,
        description=(
            "OpenAI-compatible HTTP proxy with policy-driven content "
            "type handling and pluggable compression. See docs/architecture.md."
        ),
        lifespan=lambda _app: _lifespan(_app, resolved),
    )

    # Auth stores — Redis-backed when ``redis_url`` is configured,
    # in-memory otherwise. ``build_session_store`` is the single
    # decision point.
    session_store: SessionStore = build_session_store(resolved, redis_client)

    # The Mongo client might come from either factory (user store
    # or provider store). They share a single AsyncIOMotorClient
    # when both backends point to the same Mongo URL — the factory
    # builds two distinct clients today but ``mongo_client.close()``
    # on shutdown handles each independently. Operators with both
    # backends set to ``mongo`` get two socket pools; a future
    # cycle can collapse them once ``build_*`` share a client.
    active_mongo_client = mongo_client or provider_mongo_client

    app.state.session_store = session_store
    app.state.user_store = user_store
    app.state.mongo_client = active_mongo_client
    app.state.provider_store = provider_store
    app.state.model_cache = model_cache
    app.state.redis_client = redis_client

    # Provider registry — starts empty. The provider CRUD surface
    # (TASK-002-5-b) registers per-user providers via the in-memory
    # impl that the chat router rebuilds on every request. Tests
    # that need a populated registry seed it directly via
    # ``app.state.provider_registry.register(...)``.
    provider_registry: ProviderRegistry = ProviderRegistry()
    app.state.provider_registry = provider_registry

    app.include_router(auth.router)
    app.include_router(chat_completions.router)
    app.include_router(models.router)
    app.include_router(admin.router)
    app.include_router(providers.router)

    # Legacy container probe — kept so docker-compose healthchecks
    # don't need to change. ``/admin/health`` is the operator surface
    # and requires admin role (TASK-002-3).
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
