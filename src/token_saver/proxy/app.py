"""FastAPI application factory.

Single entry point that wires together middleware, routers, and the
lifecycle context (Redis + Mongo connections). Imported lazily by
``token_saver.cli`` once ``serve`` is implemented (TASK-002-7).

TASK-002-3 adds the auth surface (``/v1/auth/login``) and attaches
the session/user stores to ``app.state`` so the auth dependencies
can pick them up via ``Depends``.
"""

from __future__ import annotations

from fastapi import FastAPI

from token_saver import __version__
from token_saver.auth.repository import InMemoryUserStore
from token_saver.auth.tokens import InMemorySessionStore
from token_saver.config import Settings, get_settings
from token_saver.proxy.routes import admin, auth, chat_completions, models

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured ``FastAPI`` instance.

    Lifecycle context (session/user stores, and later Redis pool /
    Mongo client) attaches via ``app.state`` so request handlers can
    grab it via ``request.app.state.<attr>`` without going through
    dependency injection machinery. This mirrors tokenrouter's pattern
    of a single shared client per process.

    Behaviour lands incrementally:
    - TASK-002-2: mock ``/v1/chat/completions``, ``/v1/models``, ``/admin/health``.
    - TASK-002-3 (current): ``/v1/auth/login`` + RBAC dependencies,
      ``/admin/health`` admin-only.
    - TASK-002-4: Mongo bootstrap + admin seed.
    - TASK-002-5: provider router + Provider Registry + real forward.
    - TASK-002-6: end-to-end fixture regression.
    - TASK-002-7: full CLI surface (``token-saver serve`` …).
    """
    resolved = settings or get_settings()
    app = FastAPI(
        title="Token Saver Router",
        version=__version__,
        description=(
            "OpenAI-compatible HTTP proxy with policy-driven content "
            "type handling and pluggable compression. See docs/architecture.md."
        ),
    )

    # Auth stores — TASK-002-3 in-memory impl. TASK-002-4/5 swap to
    # Mongo / Redis-backed stores without changing call sites.
    app.state.session_store = InMemorySessionStore()
    app.state.user_store = InMemoryUserStore(resolved)

    app.include_router(auth.router)
    app.include_router(chat_completions.router)
    app.include_router(models.router)
    app.include_router(admin.router)

    # Legacy container probe — kept so docker-compose healthchecks
    # don't need to change. ``/admin/health`` is the operator surface
    # and now requires admin role (TASK-002-3).
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
