"""GET /v1/models — provider-aware model listing.

TASK-002-2 stub returned a hand-curated list so the endpoint shape
was locked. TASK-002-5-b replaces the body with a per-user
:func:`ProviderStore.list_enabled_for_user` lookup — the catalogue
now reflects the caller's configured providers (regular users see
their own; admins see all rows across tenants).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from token_saver.auth.deps import get_current_user
from token_saver.models import CurrentUser, ModelCard, ModelList
from token_saver.provider.deps import get_provider_store
from token_saver.provider.store import ProviderStore

__all__ = ["router"]

router = APIRouter(tags=["models"])


@router.get(
    "/v1/models",
    response_model=ModelList,
    responses={
        200: {"description": "Provider-aware model listing."},
        401: {"description": "Missing or invalid bearer token."},
    },
)
async def list_models(
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
) -> ModelList:
    """Return the model list.

    One entry per *enabled* provider the caller owns. ``default_model``
    is the operator-chosen representative; future cycles may
    surface the full catalog (refreshed via
    ``POST /v1/providers/{id}/models/refresh``) but the MVP contract
    is "advertise one model per provider".
    """
    records = await store.list_enabled_for_user(user.id)
    cards = [
        ModelCard(
            id=record.default_model,
            owned_by=record.type,
        )
        for record in records
    ]
    return ModelList(data=cards)
