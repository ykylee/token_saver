"""End-to-end forwarding tests for ``POST /v1/chat/completions``.

The route in :mod:`token_saver.proxy.routes.chat_completions` is a
thin layer over :class:`ProviderRouter`. We exercise the wire path
with ``respx`` stubbing the upstream HTTP server:

- happy path returns the upstream body verbatim (modulo
  ``routed_provider`` tagging)
- 502 when the upstream errors
- 400 when the request carries conflicting routing signals
- extra fields tolerate vendor extensions

The provider store is populated per-test by inserting one OpenAI
row for the admin (admin sees all rows, so this also covers the
"regular user with one configured provider" path). TASK-002-5-b
upgrades the lookup from a process-wide registry to a per-user
provider store, so this fixture changed accordingly.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from token_saver.config import Settings
from token_saver.proxy import create_app

_BASE_URL = "https://api.openai.com"


@pytest.fixture
async def app_with_provider(settings: Settings) -> object:
    """An app whose provider store contains one admin-owned OpenAI provider.

    Populates the store directly (rather than via the HTTP CRUD
    surface) so individual tests don't need to manage the bearer
    token through a separate ``TestClient``. CRUD-roundtrip tests
    live in ``test_provider_routes.py``.

    Resolves the admin's user_id by reading the seeded user from
    ``app.state.user_store`` after ``create_app`` runs — the
    in-memory store auto-seeds from ``Settings.admin_email`` /
    ``Settings.admin_password`` at construction time, so this works
    without going through the lifespan.
    """
    app = create_app(settings)
    admin = await app.state.user_store.get_by_email("admin@example.com")
    assert admin is not None, "test fixture expects admin to be auto-seeded"
    await app.state.provider_store.create(
        owner_user_id=admin.id,
        name="openai-main",
        type="openai",
        base_url=_BASE_URL,
        api_key="sk-fake-test-key",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    return app


@pytest.fixture
def client_with_provider(
    app_with_provider: object,
) -> tuple[TestClient, dict[str, str]]:
    """A TestClient bound to ``app_with_provider`` plus a fresh admin bearer.

    Each TestClient triggers its own ``lifespan`` startup, which
    gives the app its own session_store. The admin token must come
    from a login *on this client* — the conftest's ``admin_auth_header``
    fixture is tied to a different app instance.
    """
    with TestClient(app_with_provider) as c:
        resp = c.post(
            "/v1/auth/login",
            json={"email": "admin@example.com", "password": "changeme-please"},
        )
        assert resp.status_code == 200, resp.text
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        yield c, headers


_UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-upstream-123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello from upstream"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
}


def _post_chat(
    client: TestClient,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> Any:
    return client.post("/v1/chat/completions", json=payload, headers=headers)


def test_happy_path_returns_upstream_response(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """The route forwards to the registered provider and adapts the body."""
    client, headers = client_with_provider
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(200, json=_UPSTREAM_BODY)
        resp = _post_chat(
            client,
            headers,
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "chatcmpl-upstream-123"
    assert body["choices"][0]["message"]["content"] == "hello from upstream"
    assert body["usage"] == _UPSTREAM_BODY["usage"]
    # The route tags the response with the routing decision.
    assert body["routed_provider"] == "openai"


def test_request_forwards_temperature_and_max_tokens(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """Sampling knobs survive the route boundary."""
    client, headers = client_with_provider
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_UPSTREAM_BODY)

    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(side_effect=_handler)
        resp = _post_chat(
            client,
            headers,
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.3,
                "max_tokens": 64,
            },
        )

    assert resp.status_code == 200, resp.text
    assert captured["temperature"] == 0.3
    assert captured["max_tokens"] == 64


def test_provider_prefix_routes_to_type(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """``openai/gpt-4o-mini`` resolves to the registered openai provider."""
    client, headers = client_with_provider
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(200, json=_UPSTREAM_BODY)
        resp = _post_chat(
            client,
            headers,
            {
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["routed_provider"] == "openai"


def test_extra_fields_pass_through_to_upstream(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """Vendor extensions are accepted by the request schema."""
    client, headers = client_with_provider
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_UPSTREAM_BODY)

    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(side_effect=_handler)
        resp = _post_chat(
            client,
            headers,
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"trace_id": "abc-123"},
                "safety_identifier": "user-42",
            },
        )

    assert resp.status_code == 200, resp.text


def test_upstream_5xx_returns_502(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """An upstream HTTP error becomes a 502 with ``provider_unavailable``."""
    client, headers = client_with_provider
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(500, text="boom")
        resp = _post_chat(
            client,
            headers,
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 502
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "provider_unavailable"


def test_upstream_connection_refused_returns_502(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """Network-level failure → 502."""
    client, headers = client_with_provider
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("nope")
        )
        resp = _post_chat(
            client,
            headers,
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 502


def test_ambiguous_provider_hint_returns_400(
    client_with_provider: tuple[TestClient, dict[str, str]],
) -> None:
    """Conflicting ``provider`` field + ``model`` prefix → 400."""
    client, headers = client_with_provider
    resp = _post_chat(
        client,
        headers,
        {
            "model": "anthropic/claude-3-5-sonnet-latest",
            "provider": "openai",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "ambiguous_provider"
