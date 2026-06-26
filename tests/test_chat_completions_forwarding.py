"""End-to-end forwarding tests for ``POST /v1/chat/completions``.

The route in :mod:`token_saver.proxy.routes.chat_completions` is a
thin layer over :class:`ProviderRouter`. We exercise the wire path
with ``respx`` stubbing the upstream HTTP server:

- happy path returns the upstream body verbatim (modulo
  ``routed_provider`` tagging)
- 502 when the upstream errors
- 400 when the request carries conflicting routing signals
- extra fields tolerate vendor extensions

The mock registry is built per-test so the lookup behaviour
(prefix vs. hint vs. default) stays deterministic.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from token_saver.config import Settings
from token_saver.provider.openai_compat import OpenAICompatProvider
from token_saver.proxy import create_app

_BASE_URL = "https://api.openai.com"


@pytest.fixture
def fake_registry() -> object:
    """A :class:`ProviderRegistry` with one registered OpenAI provider."""
    from token_saver.provider.registry import ProviderRegistry

    registry = ProviderRegistry()
    provider = OpenAICompatProvider(
        config=_openai_config(),
        client=httpx.AsyncClient(timeout=5.0),
    )
    registry.register(provider)
    return registry


@pytest.fixture
def app_with_registry(fake_registry: object, settings: Settings) -> object:
    """An app whose ``provider_registry`` is the supplied fake registry."""
    app = create_app(settings)
    app.state.provider_registry = fake_registry
    return app


@pytest.fixture
def client_with_registry(app_with_registry: object) -> TestClient:
    with TestClient(app_with_registry) as c:
        yield c


def _openai_config():
    from token_saver.provider.base import ProviderConfig

    return ProviderConfig(
        id="provider_openai_main",
        name="openai-main",
        type="openai",
        base_url=_BASE_URL,
        api_key="sk-fake-test-key",
        default_model="gpt-4o-mini",
        enabled=True,
    )


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


def test_happy_path_returns_upstream_response(client_with_registry: TestClient) -> None:
    """The route forwards to the registered provider and adapts the body."""
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(200, json=_UPSTREAM_BODY)
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
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


def test_request_forwards_temperature_and_max_tokens(client_with_registry: TestClient) -> None:
    """Sampling knobs survive the route boundary."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_UPSTREAM_BODY)

    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(side_effect=_handler)
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.3,
                "max_tokens": 64,
            },
        )

    assert resp.status_code == 200, resp.text
    assert captured["temperature"] == 0.3
    assert captured["max_tokens"] == 64


def test_provider_prefix_routes_to_type(client_with_registry: TestClient) -> None:
    """``openai/gpt-4o-mini`` resolves to the registered openai provider."""
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(200, json=_UPSTREAM_BODY)
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["routed_provider"] == "openai"


def test_extra_fields_pass_through_to_upstream(client_with_registry: TestClient) -> None:
    """Vendor extensions are accepted by the request schema."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_UPSTREAM_BODY)

    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(side_effect=_handler)
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"trace_id": "abc-123"},
                "safety_identifier": "user-42",
            },
        )

    assert resp.status_code == 200, resp.text


def test_upstream_5xx_returns_502(client_with_registry: TestClient) -> None:
    """An upstream HTTP error becomes a 502 with ``provider_unavailable``."""
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").respond(500, text="boom")
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 502
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "provider_unavailable"


def test_upstream_connection_refused_returns_502(
    client_with_registry: TestClient,
) -> None:
    """Network-level failure → 502."""
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("nope")
        )
        resp = client_with_registry.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 502


def test_ambiguous_provider_hint_returns_400(client_with_registry: TestClient) -> None:
    """Conflicting ``provider`` field + ``model`` prefix → 400."""
    resp = client_with_registry.post(
        "/v1/chat/completions",
        json={
            "model": "anthropic/claude-3-5-sonnet-latest",
            "provider": "openai",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "ambiguous_provider"
