"""End-to-end tests for ``GET /v1/models``.

TASK-002-5-b replaces the static seed list with a per-user
:class:`ProviderStore.list_enabled_for_user` lookup. The contract
under test now includes:

- Authentication required (regular + admin).
- Empty store → empty list (not a 404).
- Once a provider is registered, its ``default_model`` surfaces as
  one ``ModelCard`` in the response.
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient


def test_models_requires_auth(client: TestClient) -> None:
    """No bearer header → 401."""
    resp = client.get("/v1/models")
    assert resp.status_code == 401


def test_models_returns_empty_list_when_no_providers(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """No configured providers → empty list with the OpenAI shape."""
    resp = client.get("/v1/models", headers=admin_auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"] == []


def test_models_surfaces_configured_provider(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """A registered provider's ``default_model`` appears in the list."""
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
        create_resp = client.post(
            "/v1/providers",
            headers=admin_auth_header,
            json={
                "name": "openai-main",
                "type": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-fake-test-key",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    assert create_resp.status_code == 201, create_resp.text

    resp = client.get("/v1/models", headers=admin_auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    card = body["data"][0]
    assert card["id"] == "gpt-4o-mini"
    assert card["owned_by"] == "openai"
    assert card["object"] == "model"


def test_models_card_shape_matches_openai(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """Each ``ModelCard`` carries ``id``, ``object``, ``owned_by``."""
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
                "api_key": "sk-fake-test-key",
                "default_model": "gpt-4o-mini",
                "enabled": True,
            },
        )
    resp = client.get("/v1/models", headers=admin_auth_header)
    assert resp.status_code == 200
    for card in resp.json()["data"]:
        assert "id" in card
        assert card["object"] == "model"
        assert "owned_by" in card
