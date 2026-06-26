"""End-to-end tests for ``GET /v1/models``.

TASK-002-2 ships a static seed list — the contract under test is the
shape and the field names, not the contents (those are dynamic once
the Provider Registry lands in TASK-002-5).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_models_returns_openai_shape(client: TestClient) -> None:
    """Response matches OpenAI's ``ModelList`` contract."""
    resp = client.get("/v1/models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "list"
    assert isinstance(body["data"], list)
    assert body["data"], "seed list should be non-empty"


def test_each_card_has_required_fields(client: TestClient) -> None:
    """Every model card has ``id``, ``object="model"``, ``owned_by``."""
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    for card in resp.json()["data"]:
        assert "id" in card
        assert card["object"] == "model"
        assert "owned_by" in card


def test_seed_includes_expected_providers(client: TestClient) -> None:
    """The seed list covers every provider type the architecture supports."""
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    owners = {card["owned_by"] for card in resp.json()["data"]}
    # Architecture §2 supports OpenAI / Anthropic / Ollama / vLLM — all four
    # must appear in the seed so the route shape covers every vendor.
    assert {"openai", "anthropic", "ollama", "vllm"}.issubset(owners)
