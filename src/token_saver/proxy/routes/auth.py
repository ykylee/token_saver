"""POST /v1/auth/login — issue a bearer session.

Wire shape:

```
Request  : {"email": "...", "password": "..."}
Response 200: {"token": "...", "expires_in": 3600, "role": "admin|user", "user_id": "user_..."}
Response 401: {"error": {"message": "invalid credentials", ...}}
Response 422: validation error (FastAPI default)
```

The token is opaque to the client. We do NOT advertise session
cookies, refresh tokens, or any other credential surface in this
revision — RBAC rotation ships in P1.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from token_saver.auth.crypto import (
    InvalidCredentialsError,
    generate_token,
    verify_password,
)
from token_saver.auth.deps import get_session_store, get_user_store
from token_saver.auth.repository import UserStore
from token_saver.auth.tokens import SessionPayload, SessionStore
from token_saver.config import get_settings
from token_saver.models import LoginRequest, LoginResponse

__all__ = ["router"]

router = APIRouter(tags=["auth"])


@router.post(
    "/v1/auth/login",
    response_model=LoginResponse,
    responses={
        401: {"description": "Invalid credentials (covers unknown email and bad password)."},
        422: {"description": "Validation error (missing email/password)."},
    },
)
async def login(
    body: LoginRequest,
    user_store: UserStore = Depends(get_user_store),
    session_store: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Verify credentials and issue a session token.

    On any verification failure we return 401 with a single generic
    message — never reveal whether the email exists or the password
    was wrong. That distinction is the user-enumeration oracle we
    explicitly close here.
    """
    settings = get_settings()
    user = await user_store.get_by_email(body.email)
    if user is None:
        # Run a dummy verify to keep timing roughly constant between
        # "unknown email" and "wrong password" branches. The hash
        # below is a real argon2id result of a random password —
        # cheap to compute but impossible for an attacker to forge.
        try:
            verify_password(
                body.password,
                # Fixed pre-computed argon2id hash; matches Settings seed cost.
                "$argon2id$v=19$m=65536,t=3,p=4$"
                "c29tZXNhbHRzYWx0c2FsdA$"
                "G4pcDwA/JtJ2WQq4JzOqM7qZ8aY7J3l5QeM2sUvWf+s",
            )
        except InvalidCredentialsError:
            pass
        raise _invalid_credentials()

    try:
        verify_password(body.password, user.password_hash)
    except InvalidCredentialsError:
        raise _invalid_credentials() from None

    token = generate_token()
    ttl = settings.session_ttl_seconds
    await session_store.set(
        token,
        SessionPayload(
            user_id=user.id,
            role=user.role,
            expires_at=time.time() + ttl,
        ),
    )
    return LoginResponse(
        token=token,
        expires_in=ttl,
        role=user.role,  # type: ignore[arg-type]
        user_id=user.id,
    )


def _invalid_credentials() -> Exception:
    from fastapi import HTTPException, status

    from token_saver.models import ErrorBody, ErrorEnvelope

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorEnvelope(
            error=ErrorBody(
                message="invalid credentials",
                type="authentication_error",
                code="invalid_credentials",
            )
        ).model_dump(),
    )
