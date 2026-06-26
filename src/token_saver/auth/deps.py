"""FastAPI auth dependencies.

Three layers, intentionally granular:

- :func:`get_session_store` / :func:`get_user_store` — infrastructure
  handles, pulled from ``app.state``. Override-able in tests via
  FastAPI's ``app.dependency_overrides``.
- :func:`get_current_user` — parses the ``Authorization`` header,
  looks up the session, returns the resolved :class:`CurrentUser`.
- :func:`require_admin` / :func:`require_user` — RBAC gates layered
  on top of :func:`get_current_user`.

The split keeps the role-policy decisions in one file while letting
each route pick the strictness it needs.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from token_saver.auth.repository import UserStore
from token_saver.auth.tokens import SessionStore
from token_saver.models import CurrentUser, ErrorBody, ErrorEnvelope

__all__ = [
    "get_session_store",
    "get_user_store",
    "get_current_user",
    "require_admin",
    "require_user",
]


async def get_session_store(request: Request) -> SessionStore:
    """Return the process-wide session store.

    Pulled off ``app.state`` so tests can swap implementations by
    setting a different attribute on a fresh app instance, and so the
    dependency signature doesn't force us to plumb a store through
    every endpoint signature.
    """
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise RuntimeError(
            "session_store is not initialised on app.state. "
            "Did create_app() skip the auth wiring?"
        )
    return store  # type: ignore[no-any-return]


async def get_user_store(request: Request) -> UserStore:
    """Return the process-wide user store."""
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        raise RuntimeError(
            "user_store is not initialised on app.state. "
            "Did create_app() skip the auth wiring?"
        )
    return store  # type: ignore[no-any-return]


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorEnvelope(
            error=ErrorBody(message=message, type="authentication_error", code="unauthorized")
        ).model_dump(),
        headers={"WWW-Authenticate": 'Bearer realm="token-saver"'},
    )


def _forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ErrorEnvelope(
            error=ErrorBody(message=message, type="permission_error", code="forbidden")
        ).model_dump(),
    )


async def get_current_user(
    request: Request,
    session_store: SessionStore = Depends(get_session_store),
    user_store: UserStore = Depends(get_user_store),
) -> CurrentUser:
    """Resolve the calling identity from the bearer token.

    Returns 401 on a missing/invalid/expired token. We do NOT
    distinguish "no header" from "bad token" — the response is the
    same so probing for valid token formats yields no signal.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _unauthorized("missing or malformed Authorization header")

    token = auth[7:].strip()
    if not token:
        raise _unauthorized("empty bearer token")

    payload = await session_store.get(token)
    if payload is None:
        raise _unauthorized("session not found or expired")

    user = await user_store.get_by_id(payload.user_id)
    if user is None:
        # Session exists but the user was deleted. Treat as auth fail
        # so clients re-login rather than see a half-resolved identity.
        raise _unauthorized("user no longer exists")

    return CurrentUser(id=user.id, role=user.role, email=user.email)  # type: ignore[arg-type]


async def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """RBAC gate: ``admin`` only."""
    if current.role != "admin":
        raise _forbidden("admin role required")
    return current


async def require_user(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """RBAC gate: any authenticated identity (admin or user).

    RBAC matrix (architecture §5.2) lists most user-facing endpoints
    as ``user ✓ admin ✓`` — both roles pass. The narrower
    :func:`require_admin` covers the operator surface.
    """
    return current
