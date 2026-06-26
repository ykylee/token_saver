"""FastAPI application factory.

TASK-002-4 wires :func:`auth.factory.build_user_store` into the
boot path and adds a FastAPI lifespan that:

1. **startup** — runs ``MongoUserStore.ensure_indexes`` (no-op for
   in-memory) and idempotently seeds the admin via :func:`auth.seed`.
2. **shutdown** — closes the underlying motor client if any.

In-memory mode keeps the old zero-config behaviour for tests and
local development; production / docker-compose flip
``TOKEN_SAVER_USER_STORE_BACKEND=mongo`` and the same factory code
brings up the real collection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from token_saver import __version__
from token_saver.auth.factory import build_user_store
from token_saver.auth.repository import MongoUserStore, UserStore
from token_saver.auth.seed import SeedAdminSkipped, seed_admin
from token_saver.auth.tokens import InMemorySessionStore, SessionStore
from token_saver.config import Settings, get_settings
from token_saver.proxy.routes import admin, auth, chat_completions, models

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

    try:
        await seed_admin(settings, user_store)
    except SeedAdminSkipped:
        pass

    yield

    mongo_client: AsyncIOMotorClient | None = app.state.mongo_client
    if mongo_client is not None:
        mongo_client.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured ``FastAPI`` instance.

    Lifecycle context (session/user stores, Mongo client) attaches
    via ``app.state`` so request handlers can grab it via
    ``request.app.state.<attr>`` without going through dependency
    injection machinery.

    Behaviour lands incrementally:
    - TASK-002-2: mock ``/v1/chat/completions``, ``/v1/models``, ``/admin/health``.
    - TASK-002-3: ``/v1/auth/login`` + RBAC dependencies + ``/admin/health`` admin-only.
    - TASK-002-4 (current): Mongo user-store backend + admin seed + indexes
      via FastAPI lifespan.
    - TASK-002-5: provider router + Provider Registry + real forward.
    - TASK-002-6: end-to-end fixture regression.
    - TASK-002-7: full CLI surface (``token-saver serve`` …).
    """
    resolved = settings or get_settings()

    # Build the user store up-front so we can reference it inside the
    # lifespan closure. ``build_user_store`` returns the optional mongo
    # client so we can close it on shutdown.
    user_store, mongo_client = build_user_store(resolved)

    app = FastAPI(
        title="Token Saver Router",
        version=__version__,
        description=(
            "OpenAI-compatible HTTP proxy with policy-driven content "
            "type handling and pluggable compression. See docs/architecture.md."
        ),
        lifespan=lambda _app: _lifespan(_app, resolved),
    )

    # Auth stores — TASK-002-3 session store is still in-memory; the
    # Redis-backed impl slots in next to it in TASK-002-5. We attach
    # the user store here; the factory is the only call site that
    # knows about the backend. Type annotations live on the module-level
    # aliases (not inline ``app.state.x: T = ...``) so mypy doesn't
    # complain about in-assignment annotations on a non-self attribute.
    session_store: SessionStore = InMemorySessionStore()
    app.state.session_store = session_store
    app.state.user_store = user_store
    app.state.mongo_client = mongo_client

    app.include_router(auth.router)
    app.include_router(chat_completions.router)
    app.include_router(models.router)
    app.include_router(admin.router)

    # Legacy container probe — kept so docker-compose healthchecks
    # don't need to change. ``/admin/health`` is the operator surface
    # and requires admin role (TASK-002-3).
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
