"""Validation tests for ``POST /v1/chat/completions``.

Behavioural / forwarding tests live in ``test_chat_completions_forwarding.py``
(those need a populated ``ProviderStore`` + ``respx``). This file
stays focused on request-shape validation and the explicit
``stream=true`` guard, which don't depend on the provider layer.

TASK-002-5-b note: the route now requires authentication (per-user
provider lookup), so every test issues a bearer header. The default
``admin_auth_header`` fixture is sufficient because the admin sees
all rows (and in the empty-store case, sees none — same 503 as a
regular user).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _post_chat(
    client: TestClient, payload: dict[str, Any], headers: dict[str, str]
) -> Any:
    return client.post("/v1/chat/completions", json=payload, headers=headers)


@pytest.mark.parametrize(
    "payload, error_substring",
    [
        ({"messages": [{"role": "user", "content": "hi"}]}, "model"),
        ({"model": "gpt-4o-mini"}, "messages"),
        ({"model": "gpt-4o-mini", "messages": []}, "messages"),
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
    client: TestClient,
    admin_auth_header: dict[str, str],
    payload: dict[str, Any],
    error_substring: str,
) -> None:
    """Pydantic validation surfaces as 422 with field names in the error body."""
    resp = _post_chat(client, payload, admin_auth_header)
    assert resp.status_code == 422, resp.text
    assert error_substring in resp.text


def test_temperature_out_of_range_returns_422(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """Sampling knobs honour OpenAI's bounds — temperature > 2 must reject."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 3.5,
        },
        admin_auth_header,
    )
    assert resp.status_code == 422


def test_stream_true_returns_400_with_explicit_error(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """``stream=true`` is gated until TASK-002-6 — we 400, not 422, with a clear code."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        admin_auth_header,
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "stream_not_supported"
    assert detail["error"]["param"] == "stream"


def test_no_provider_registered_returns_503(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """Empty provider store → 503 ``no_provider_available``.

    Sanity check that the auth + per-user lookup is wired before
    forwarding tests run. ``conftest``'s default ``app`` fixture ships
    with an empty provider store, so this catches the case where the
    wiring regresses.
    """
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
        admin_auth_header,
    )
    assert resp.status_code == 503
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["error"]["code"] == "no_provider_available"


def test_unknown_provider_prefix_returns_503(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """A model prefix that no registered provider matches → 503."""
    resp = _post_chat(
        client,
        {
            "model": "unknown-vendor/whatever",
            "messages": [{"role": "user", "content": "hi"}],
        },
        admin_auth_header,
    )
    assert resp.status_code == 503


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    """No bearer header → 401 (route now requires auth post-5-b)."""
    resp = _post_chat(
        client,
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
        {},
    )
    assert resp.status_code == 401
