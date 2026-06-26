"""GET /v1/models — provider-aware model listing.

TASK-002-2 stub returns a hand-curated list so the endpoint shape is
locked. TASK-002-5 will replace the body with a Provider Registry
lookup that walks the user's configured providers, caches the result
in Redis (TTL 1h), and filters by ``user_id``.
"""

from __future__ import annotations

from fastapi import APIRouter

from token_saver.models import ModelCard, ModelList

__all__ = ["router"]

router = APIRouter(tags=["models"])


# Static seed list — purely cosmetic until the Provider Registry lands.
# Models here mirror what we expect Ollama / vLLM to advertise so the
# fixture test can assert a stable shape.
_SEED_MODELS: tuple[ModelCard, ...] = (
    ModelCard(
        id="gpt-4o-mini",
        owned_by="openai",
    ),
    ModelCard(
        id="claude-3-5-sonnet-latest",
        owned_by="anthropic",
    ),
    ModelCard(
        id="llama3.1:8b",
        owned_by="ollama",
    ),
    ModelCard(
        id="Qwen/Qwen2.5-7B-Instruct",
        owned_by="vllm",
    ),
)


@router.get(
    "/v1/models",
    response_model=ModelList,
    responses={200: {"description": "Provider-aware model listing."}},
)
async def list_models() -> ModelList:
    """Return the model list.

    TASK-002-2: returns a static seed list. TASK-002-5 will resolve
    through the Provider Registry and respect the calling user's
    tenant boundary.
    """
    return ModelList(data=list(_SEED_MODELS))
