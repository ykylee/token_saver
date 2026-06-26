"""End-to-end tests for ``POST /v1/auth/login`` and the bearer middleware.

Two surfaces:

1. **Login**: success path, 401 on bad password, 401 on unknown email
   (same generic message — no enumeration oracle), 422 on bad input.
2. **Bearer middleware**: a freshly-issued token unlocks RBAC-gated
   endpoints; missing / malformed / expired tokens return 401.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

# ----- Login -----


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def test_login_with_valid_admin_credentials_returns_token(
    client: TestClient,
) -> None:
    """Admin login issues a bearer token with the expected envelope."""
    resp = _login(client, "admin@example.com", "changeme-please")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Wire-shape lock-in: clients (incl. the OpenAI SDK) parse these.
    assert isinstance(body["token"], str) and len(body["token"]) >= 32
    assert body["expires_in"] >= 60
    assert body["role"] == "admin"
    assert body["user_id"].startswith("user_")


def test_login_with_wrong_password_returns_401(client: TestClient) -> None:
    """Wrong password → 401 with a generic message; no enumeration hint."""
    resp = _login(client, "admin@example.com", "wrong")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["error"]["code"] == "invalid_credentials"


def test_login_with_unknown_email_returns_same_401(client: TestClient) -> None:
    """Unknown email → identical 401 envelope.

    We never distinguish "no such user" from "wrong password" — that's
    the user-enumeration leak we explicitly close.
    """
    resp = _login(client, "nobody@example.com", "anything")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["error"]["code"] == "invalid_credentials"
    # Same message wording as the wrong-password branch.
    assert body["detail"]["error"]["message"] == "invalid credentials"


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing both
        {"email": "admin@example.com"},  # missing password
        {"password": "changeme-please"},  # missing email
        {"email": "", "password": "x"},  # empty email
        {"email": "x", "password": ""},  # empty password
    ],
)
def test_login_validation_returns_422(client: TestClient, payload: dict[str, Any]) -> None:
    """Bad shape → 422 via FastAPI/Pydantic, not 401."""
    resp = client.post("/v1/auth/login", json=payload)
    assert resp.status_code == 422


def test_login_response_carries_correct_ttl(
    client: TestClient, settings
) -> None:  # noqa: ARG001
    """``expires_in`` echoes the configured TTL — clients use it to schedule refresh."""
    resp = _login(client, "admin@example.com", "changeme-please")
    assert resp.status_code == 200
    assert resp.json()["expires_in"] == settings.session_ttl_seconds


# ----- Bearer middleware -----


def test_missing_authorization_returns_401(client: TestClient) -> None:
    """No header → 401 with ``WWW-Authenticate`` so clients pick the right scheme."""
    resp = client.get("/admin/health")
    assert resp.status_code == 401
    assert "bearer" in resp.headers.get("www-authenticate", "").lower()


def test_malformed_authorization_returns_401(client: TestClient) -> None:
    """``Authorization: <not Bearer>`` → 401."""
    resp = client.get(
        "/admin/health",
        headers={"Authorization": "Token abc123"},
    )
    assert resp.status_code == 401


def test_empty_bearer_returns_401(client: TestClient) -> None:
    """``Authorization: Bearer   `` (empty token) → 401."""
    resp = client.get(
        "/admin/health",
        headers={"Authorization": "Bearer   "},
    )
    assert resp.status_code == 401


def test_unknown_token_returns_401(client: TestClient) -> None:
    """Well-formed bearer with a token the store doesn't know → 401."""
    resp = client.get(
        "/admin/health",
        headers={"Authorization": "Bearer not-a-real-token-1234567890"},
    )
    assert resp.status_code == 401


def test_issued_token_unlocks_admin_endpoint(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """End-to-end: login → use the returned token against /admin/health."""
    resp = client.get("/admin/health", headers=admin_auth_header)
    assert resp.status_code == 200


def test_user_token_cannot_reach_admin_endpoint(
    client: TestClient, user_auth_header: dict[str, str]
) -> None:
    """A non-admin token gets through auth (401 vs 403 distinction)."""
    resp = client.get("/admin/health", headers=user_auth_header)
    assert resp.status_code == 403


# ----- Session lifecycle -----


@pytest.mark.asyncio
async def test_session_store_lazy_evicts_expired() -> None:
    """Sanity: the in-memory store expires payloads on read, not on write.

    This is a unit-style test of the store itself, no HTTP layer in
    the way. The whole point is to lock in the eviction policy before
    Redis arrives and we have to re-prove it.
    """
    import time

    from token_saver.auth.tokens import InMemorySessionStore, SessionPayload

    store = InMemorySessionStore()
    await store.set(
        "tok",
        SessionPayload(user_id="u1", role="user", expires_at=time.time() - 1),
    )
    assert await store.get("tok") is None


@pytest.mark.asyncio
async def test_session_store_revoke_removes_payload() -> None:
    """Revoke drops the payload immediately."""
    import time

    from token_saver.auth.tokens import InMemorySessionStore, SessionPayload

    store = InMemorySessionStore()
    await store.set(
        "tok",
        SessionPayload(user_id="u1", role="user", expires_at=time.time() + 60),
    )
    await store.revoke("tok")
    assert await store.get("tok") is None
