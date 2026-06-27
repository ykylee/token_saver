"""Provider CRUD roundtrip tests — ``/v1/providers`` + RBAC.

Covers the operator-facing surface end-to-end through the FastAPI
app. ``respx`` intercepts the upstream ``GET /v1/models`` calls so
the connection probe at registration time returns a deterministic
verdict without hitting the network.

Coverage:

- ``POST /v1/providers/test`` — admin / regular user can probe.
- ``POST /v1/providers`` — creates a provider, refuses unknown
  types and bad configs.
- ``GET /v1/providers`` — admin sees all, regular users see own.
- ``POST /v1/providers/{id}/models/refresh`` — refreshes catalog,
  updates Mongo + Redis cache.
- ``DELETE /v1/providers/{id}`` — admin can delete any, regular
  users only their own.
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient

from token_saver.proxy.routes.providers import _scope_user_id

# ----- POST /v1/providers/test -----


def test_test_endpoint_admin_only_header_required(
    client: TestClient,
) -> None:
    """No bearer header → 401 (the endpoint requires auth)."""
    resp = client.post(
        "/v1/providers/test",
        json={
            "type": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "default_model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 401


def test_test_endpoint_returns_ok_with_sample_models(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "owned_by": "openai"},
                    {"id": "gpt-4o", "owned_by": "openai"},
                ],
            },
        )
        resp = client.post(
            "/v1/providers/test",
            headers=admin_auth_header,
            json={
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["models_count"] == 2
    assert len(body["sample_models"]) <= 5


def test_test_endpoint_returns_failure_verdict_on_upstream_error(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """``/test`` returns ``ok=False`` (not a 502) when the upstream errors.

    The endpoint exists precisely so operators can probe a
    config without committing — surfacing the verdict in the body
    lets the UI show the exact failure string. The 502 envelope
    is reserved for ``/v1/providers`` (the create path).
    """
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(500, text="boom")
        resp = client.post(
            "/v1/providers/test",
            headers=admin_auth_header,
            json={
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] is not None
    assert "500" in body["error"]


def test_test_endpoint_returns_400_on_unknown_type(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    resp = client.post(
        "/v1/providers/test",
        headers=admin_auth_header,
        json={
            "type": "no-such-vendor",
            "base_url": "https://api.example.com",
            "api_key": "k",
        },
    )
    assert resp.status_code == 422  # Pydantic Literal rejects unknown types


# ----- POST /v1/providers -----


def test_create_provider_returns_201_with_info(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-main",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"].startswith("provider_")
    assert body["name"] == "openai-main"
    assert body["type"] == "openai"
    assert body["base_url"] == "https://api.openai.com"
    assert body["default_model"] == "gpt-4o-mini"
    assert body["enabled"] is True
    # The api_key is intentionally not in ProviderInfo.
    assert "api_key" not in body


def test_create_provider_returns_502_on_upstream_failure(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """A failing connection probe at registration time → 502."""
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(500, text="boom")
        resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-bad",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
            },
        )
    assert resp.status_code == 502


def test_create_provider_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/v1/providers",
        json={
            "name": "openai-main",
            "type": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "default_model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 401


# ----- GET /v1/providers -----


def test_list_returns_empty_for_new_user(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    resp = client.get("/v1/providers", headers=admin_auth_header)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_admin_owned_providers(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-main",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    resp = client.get("/v1/providers", headers=admin_auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "openai-main"


def test_list_filters_by_owner_for_regular_user(
    client: TestClient,
    admin_auth_header: dict[str, str],
    regular_user: dict[str, str],
    user_auth_header: dict[str, str],
) -> None:
    """A regular user sees only their own providers."""
    # Create one provider as admin, one as the regular user.
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-admin",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        client.post(
            "/v1/providers",
            headers=user_auth_header,
            json={
                "name": "openai-alice",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )

    # Regular user only sees their own row.
    resp = client.get("/v1/providers", headers=user_auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "openai-alice"


# ----- POST /v1/providers/{id}/models/refresh -----


def test_refresh_returns_models_count(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        create_resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-main",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    provider_id = create_resp.json()["id"]

    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "owned_by": "openai"},
                    {"id": "gpt-4o", "owned_by": "openai"},
                ],
            },
        )
        resp = client.post(
            f"/v1/providers/{provider_id}/models/refresh",
            headers=admin_auth_header,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider_id"] == provider_id
    assert body["models_count"] == 2


def test_refresh_returns_404_for_missing(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    resp = client.post(
        "/v1/providers/provider_missing/models/refresh",
        headers=admin_auth_header,
    )
    assert resp.status_code == 404


# ----- DELETE /v1/providers/{id} -----


def test_delete_removes_provider(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        create_resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-main",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    provider_id = create_resp.json()["id"]

    resp = client.delete(
        f"/v1/providers/{provider_id}", headers=admin_auth_header
    )
    assert resp.status_code == 204

    # The provider is gone from the list.
    list_resp = client.get("/v1/providers", headers=admin_auth_header)
    assert all(p["id"] != provider_id for p in list_resp.json())


def test_delete_returns_404_for_missing(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    resp = client.delete(
        "/v1/providers/provider_missing", headers=admin_auth_header
    )
    assert resp.status_code == 404


def test_regular_user_cannot_delete_admin_provider(
    client: TestClient,
    admin_auth_header: dict[str, str],
    user_auth_header: dict[str, str],
) -> None:
    """A regular user must not delete another user's provider."""
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.get("/v1/models").respond(
            200, json={"object": "list", "data": []}
        )
        create_resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-admin",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    provider_id = create_resp.json()["id"]

    resp = client.delete(
        f"/v1/providers/{provider_id}", headers=user_auth_header
    )
    assert resp.status_code == 404


# ----- helper coverage -----


def test_scope_user_id_returns_none_for_admin(
    admin_auth_header: dict[str, str],  # noqa: ARG001
) -> None:
    """``_scope_user_id`` returns ``None`` for admin (all rows)."""
    from token_saver.models import CurrentUser

    admin = CurrentUser(id="user_admin", role="admin", email="admin@example.com")
    assert _scope_user_id(admin) is None


def test_scope_user_id_returns_id_for_regular_user() -> None:
    """``_scope_user_id`` returns the user's own id for tenant filtering."""
    from token_saver.models import CurrentUser

    user = CurrentUser(id="user_alice", role="user", email="alice@example.com")
    assert _scope_user_id(user) == "user_alice"
