"""FastAPI application factory.

Single entry point that wires together middleware, routers, and the
lifecycle context (Redis + Mongo connections). Imported lazily by
``token_saver.cli`` once ``serve`` is implemented (TASK-002-7).

TASK-002-2 mounts the three first-class route groups (``chat``,
``models``, ``admin``) and keeps the legacy ``/healthz`` for
container-probe back-compat.
"""

from __future__ import annotations

from fastapi import FastAPI

from token_saver import __version__
from token_saver.proxy.routes import admin, chat_completions, models

__all__ = ["create_app"]


def create_app() -> FastAPI:
    """Build and return a configured ``FastAPI`` instance.

    Lifecycle context (Redis pool, Mongo client) attaches via
    ``app.state`` so request handlers can grab it via
    ``request.app.state.<attr>`` without going through dependency
    injection machinery. This mirrors tokenrouter's pattern of a
    single shared client per process.

    Behaviour lands incrementally:
    - TASK-002-2 (current): mock ``/v1/chat/completions``,
      ``/v1/models``, ``/admin/health``.
    - TASK-002-3: auth middleware + ``/v1/auth/login`` + RBAC deps.
    - TASK-002-4: Mongo bootstrap + admin seed.
    - TASK-002-5: provider router + Provider Registry + real forward.
    - TASK-002-6: end-to-end fixture regression.
    - TASK-002-7: full CLI surface (``token-saver serve`` …).
    """
    app = FastAPI(
        title="Token Saver Router",
        version=__version__,
        description=(
            "OpenAI-compatible HTTP proxy with policy-driven content "
            "type handling and pluggable compression. See docs/architecture.md."
        ),
    )

    app.include_router(chat_completions.router)
    app.include_router(models.router)
    app.include_router(admin.router)

    # Legacy container probe — TASK-002-2 keeps it so docker-compose
    # healthchecks don't need to change. ``/admin/health`` is the
    # operator surface and gains RBAC in TASK-002-3.
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
