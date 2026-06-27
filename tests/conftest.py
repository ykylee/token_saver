"""Shared pytest fixtures and configuration.

TASK-002-3 added the auth surface, so the fixtures grew:

- ``admin_user`` / ``regular_user`` — pre-seeded identities.
- ``admin_token`` / ``user_token`` — issued bearer tokens.
- ``admin_auth_header`` / ``user_auth_header`` — drop-in ``headers``
  dicts for ``TestClient`` calls.

The auth flow itself is exercised through ``create_app``'s default
``InMemoryUserStore`` (seeded from ``Settings.admin_email`` /
``Settings.admin_password``), so logging in via ``POST /v1/auth/login``
goes through the same code path as a real deployment — no parallel
test-only shortcut.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from token_saver import __version__
from token_saver.config import Settings, get_settings
from token_saver.proxy import create_app

__all__ = [
    "settings",
    "app",
    "client",
    "__version__",
    "admin_user",
    "regular_user",
    "admin_auth_header",
    "user_auth_header",
]

# Match the .env.example defaults. The Settings dataclass owns the
# canonical values; this constant exists so tests can build a fresh
# ``Settings`` without depending on the operator's environment.
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "changeme-please"


@pytest.fixture
def settings() -> Settings:
    """A fresh ``Settings`` instance with known admin credentials.

    Bypasses the lru_cache singleton so each test gets an isolated
    Settings — critical because ``InMemoryUserStore(settings)`` reads
    admin_email/admin_password at construction time.

    ``redis_url`` is explicitly cleared to ``""`` so the provider
    factory's Redis client stays un-built and tests don't need a
    live Redis. Tests that want Redis behaviour set it explicitly.
    """
    get_settings.cache_clear()
    return Settings(
        admin_email=ADMIN_EMAIL,
        admin_password=ADMIN_PASSWORD,
        redis_url="",
    )


@pytest.fixture
def app(settings: Settings) -> object:
    """A fresh FastAPI app per test (no shared state)."""
    return create_app(settings)


@pytest.fixture
def client(app: object) -> Iterator[TestClient]:
    """A ``TestClient`` bound to the test's app instance."""
    with TestClient(app) as c:  # type: ignore[arg-type]
        yield c


# ----- Auth helpers -----


@pytest.fixture
def admin_user(client: TestClient) -> dict[str, str]:
    """Log in as the seeded admin and return the resolved identity."""
    resp = client.post(
        "/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {"user_id": body["user_id"], "role": body["role"], "email": ADMIN_EMAIL}


@pytest.fixture
def admin_auth_header(client: TestClient) -> dict[str, str]:
    """A ready-to-use ``{"Authorization": "Bearer <token>"}`` header for admin."""
    resp = client.post(
        "/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def regular_user(app: object) -> dict[str, str]:
    """Seed a non-admin user directly on the app's user store.

    We bypass ``/v1/auth/login`` here because there's no admin-only
    "create user" endpoint yet — that ships in TASK-002-4 alongside
    Mongo bootstrap. The store is reachable via ``app.state.user_store``.
    """
    user_store = app.state.user_store  # type: ignore[attr-defined]
    record = user_store.add_user(
        email="alice@example.com",
        password="alice-secret-123",
        role="user",
    )
    return {"user_id": record.id, "email": record.email, "role": record.role}


@pytest.fixture
def user_auth_header(
    client: TestClient, regular_user: dict[str, str]
) -> dict[str, str]:
    """A ready-to-use bearer header for the seeded non-admin user."""
    resp = client.post(
        "/v1/auth/login",
        json={"email": regular_user["email"], "password": "alice-secret-123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
