"""FastAPI application factory.

Single entry point that wires together middleware, routers, and the
lifecycle context (Redis + Mongo connections). Imported lazily by
``token_saver.cli`` once ``serve`` is implemented (TASK-002-7).

TASK-002-1 ships the factory signature only. The routers are stubs —
each lifecycle step (auth, rate limit, CCR lookup, etc.) lands in its
own sub-task (TASK-002-2 .. TASK-002-7).
"""

from __future__ import annotations

from fastapi import FastAPI

__all__ = ["create_app"]


def create_app() -> FastAPI:
    """Build and return a configured ``FastAPI`` instance.

    Lifecycle context (Redis pool, Mongo client) is attached via
    ``app.state`` so request handlers can grab it via
    ``request.app.state.<attr>`` without going through dependency
    injection machinery. This mirrors tokenrouter's pattern of a
    single shared client per process.

    Behaviour lands incrementally:
    - TASK-002-2: ``/v1/chat/completions`` mock endpoint.
    - TASK-002-3: auth middleware + ``/v1/auth/login``.
    - TASK-002-4: Mongo bootstrap + admin seed.
    - TASK-002-5: provider router + Provider Registry.
    - TASK-002-6: end-to-end fixture regression.
    """
    app = FastAPI(
        title="Token Saver Router",
        version="0.1.0a0",
        description=(
            "OpenAI-compatible HTTP proxy with policy-driven content "
            "type handling and pluggable compression. See docs/architecture.md."
        ),
    )

    # Placeholder health endpoint — TASK-002-2 will move this under /admin/health
    # with RBAC; for the skeleton it lives at root so docker-compose smoke can hit it.
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
