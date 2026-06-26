"""End-to-end tests for ``POST /v1/chat/completions``.

Goals (TASK-002-2):
1. Happy path: valid request → 200 with OpenAI-compatible response shape.
2. Validation: missing/empty/invalid fields → 422.
3. Stream guard: ``stream=true`` → 400 with explicit error.
4. Determinism: response is stable enough for fixture regression.
5. Mock header: clients can confirm they're talking to the skeleton.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _post_chat(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/v1/chat/completions", json=payload)


def test_happy_path_returns_openai_shape(client: TestClient) -> None:
    """A minimal request yields a fully-populated OpenAI-compatible response."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say hi in 3 words."},
        ],
    }
    resp = _post_chat(client, payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level keys (OpenAI ChatCompletion contract).
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o-mini"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert body["created"] > 0

    # Choices.
    assert isinstance(body["choices"], list)
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert "token-saver mock" in choice["message"]["content"]

    # Usage accounting.
    usage = body["usage"]
    assert usage["prompt_tokens"] >= 1
    assert usage["completion_tokens"] >= 1
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    # routed_provider is wired in TASK-002-5 — must be present and null for now.
    assert "routed_provider" in body
    assert body["routed_provider"] is None


def test_request_echoes_unknown_fields() -> None:
    """Extra fields from clients are accepted, not rejected.

    Vendors extend the OpenAI schema (e.g. Anthropic-style metadata,
    OpenAI's own ``safety_identifier``). The proxy must not 400 on
    fields it doesn't model — that would break every client on a
    schema bump.
    """
    from fastapi.testclient import TestClient

    from token_saver.proxy import create_app

    app = create_app()
    with TestClient(app) as c:
        resp = c.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"trace_id": "abc-123"},  # not in our schema
                "safety_identifier": "user-42",      # not in our schema either
            },
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "payload, error_substring",
    [
        # Missing ``model``.
        (
            {"messages": [{"role": "user", "content": "hi"}]},
            "model",
        ),
        # Missing ``messages``.
        (
            {"model": "gpt-4o-mini"},
            "messages",
        ),
        # Empty ``messages`` list.
        (
            {"model": "gpt-4o-mini", "messages": []},
            "messages",
        ),
        # Unknown role.
        (
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "alien", "content": "hi"}],
            },
            "role",
        ),
    ],
)
def test_validation_errors_return_422(
    client: TestClient, payload: dict[str, Any], error_substring: str
) -> None:
    """Pydantic validation surfaces as 422 with field names in the error body."""
    resp = _post_chat(client, payload)
    assert resp.status_code == 422, resp.text
    # FastAPI's default validation envelope mentions the offending field.
    assert error_substring in resp.text


def test_temperature_out_of_range_returns_422(client: TestClient) -> None:
    """Sampling knobs honour OpenAI's bounds — temperature > 2 must reject."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 3.5,
        },
    )
    assert resp.status_code == 422


def test_stream_true_returns_400_with_explicit_error(client: TestClient) -> None:
    """``stream=true`` is gated until TASK-002-6 — we 400, not 422, with a clear code."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    # Our HTTPException wraps the envelope under ``detail``.
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "stream_not_supported"
    assert detail["error"]["param"] == "stream"


def test_last_user_message_drives_mock_content(client: TestClient) -> None:
    """Mock content references the most recent user message — fixture-friendly."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "you are a parrot"},
                {"role": "user", "content": "hello there"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    # The mock embeds the user-message char count so we can assert it.
    assert "11 chars" in content  # len("hello there") == 11


def test_mock_response_header_surfaces_mock_state() -> None:
    """The mock flag lives on the response header so clients can confirm
    they're talking to the skeleton without parsing the body.
    """
    from fastapi.testclient import TestClient

    from token_saver.proxy import create_app
    from token_saver.proxy.routes.chat_completions import (
        MOCK_HEADER,
        MOCK_HEADER_VALUE,
    )

    app = create_app()
    with TestClient(app) as c:
        resp = c.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get(MOCK_HEADER) == MOCK_HEADER_VALUE
