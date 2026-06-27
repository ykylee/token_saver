"""POST /v1/chat/completions — OpenAI-compatible chat proxy.

TASK-002-5-b upgrades the lookup from a process-wide registry to a
**per-user** registry built from the provider store on each
request:

1. **Auth** — ``get_current_user`` resolves the caller. Regular
   users see their own rows; admins get all rows across tenants
   (``provider_store.list_enabled_for_user`` handles the filter).
2. **Build a transient :class:`ProviderRegistry`** from those
   records. Each ``ProviderRecord`` decrypts ``api_key`` and
   ``base_url`` on the way out of the store, so building the
   ``BaseProvider`` is straightforward.
3. **Route** through :class:`ProviderRouter` — prefix parsing,
   hint agreement, default fallback. Same policy as TASK-002-5-a;
   the only thing that's different is the registry source.
4. **Forward** to the resolved provider's ``invoke`` and adapt
   the response so the client sees the OpenAI shape it came in
   with.

The per-request registry cost is bounded — building it is just
constructing dataclasses and an ``httpx.AsyncClient`` per provider,
both O(1) per row. We can move to a cached registry if profiling
shows it later.

Validation policy:

- ``stream=true`` is still gated (400) — streaming lands in
  TASK-002-6.
- Vendor / network errors are translated into 502 Bad Gateway
  with the same ``ErrorEnvelope`` shape we use everywhere else.
- Empty per-user registry raises 503 — the proxy is healthy but
  has nothing to forward to for this caller.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from token_saver.auth.deps import get_current_user
from token_saver.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CurrentUser,
    ErrorBody,
    ErrorEnvelope,
)
from token_saver.provider.base import (
    InvokeOptions,
    ProviderConfig,
    ProviderConnectionError,
    ProviderResponseError,
)
from token_saver.provider.deps import get_provider_store
from token_saver.provider.registry import (
    ProviderRegistry,
    UnknownProviderTypeError,
)
from token_saver.provider.router import (
    AmbiguousProviderError,
    NoProviderAvailableError,
    ProviderRouter,
)
from token_saver.provider.store import ProviderStore

__all__ = ["router"]

router = APIRouter(tags=["chat"])


def _options_from_request(body: ChatCompletionRequest) -> InvokeOptions:
    """Project the request body onto :class:`InvokeOptions`.

    Centralised so future request-field additions land in one
    place rather than scattered across the route.
    """
    return InvokeOptions(
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        stop=body.stop,
        presence_penalty=body.presence_penalty,
        frequency_penalty=body.frequency_penalty,
        user=body.user,
    )


def _build_registry(
    records: list, registry: ProviderRegistry
) -> ProviderRegistry:
    """Populate ``registry`` with one ``BaseProvider`` per record.

    Returns ``registry`` for call-site convenience. Each call to
    this function builds a *fresh* registry; the chat route never
    reuses one across requests so a stale provider state can never
    leak between users.
    """
    for record in records:
        config = ProviderConfig(
            id=record.id,
            name=record.name,
            type=record.type,
            base_url=record.base_url,
            api_key=record.api_key,
            default_model=record.default_model,
            enabled=record.enabled,
        )
        try:
            provider = ProviderRegistry.from_config(config)
        except UnknownProviderTypeError:
            # A row with an unknown type (typo in the DB) is
            # silently skipped. Operators see it on
            # ``GET /v1/providers`` and fix it there.
            continue
        registry.register(provider)
    return registry


@router.post(
    "/v1/chat/completions",
    response_model=ChatCompletionResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "Bad request (e.g. ambiguous routing, stream unsupported)"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        502: {"model": ErrorEnvelope, "description": "Provider connection or response error"},
        503: {"model": ErrorEnvelope, "description": "No provider available"},
    },
)
async def create_chat_completion(
    body: ChatCompletionRequest,
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
) -> ChatCompletionResponse:
    """OpenAI-compatible chat completion endpoint.

    The route is the only place in the request lifecycle that
    talks to a provider. Everything else (auth, rate limit,
    CCR-lite) plugs in around it as dependencies or middleware.
    """
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorEnvelope(
                error=ErrorBody(
                    message="stream=true is not supported yet (planned for TASK-002-6).",
                    type="invalid_request_error",
                    param="stream",
                    code="stream_not_supported",
                )
            ).model_dump(),
        )

    records = await store.list_enabled_for_user(user.id)
    registry = _build_registry(records, ProviderRegistry())
    router_ = ProviderRouter(registry)
    try:
        provider, response = await router_.invoke(
            model_spec=body.model,
            messages=body.messages,
            options=_options_from_request(body),
            provider_hint=body.provider,
        )
        response.routed_provider = provider.config.type  # type: ignore[assignment]
        return response
    except NoProviderAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorEnvelope(
                error=ErrorBody(
                    message=str(exc),
                    type="capacity_error",
                    code="no_provider_available",
                )
            ).model_dump(),
        ) from exc
    except AmbiguousProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorEnvelope(
                error=ErrorBody(
                    message=str(exc),
                    type="invalid_request_error",
                    code="ambiguous_provider",
                )
            ).model_dump(),
        ) from exc
    except (ProviderConnectionError, ProviderResponseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorEnvelope(
                error=ErrorBody(
                    message=str(exc),
                    type="upstream_error",
                    code="provider_unavailable",
                )
            ).model_dump(),
        ) from exc
    finally:
        # Per-request providers own their httpx clients; close them
        # so we don't leak sockets during traffic spikes.
        await registry.aclose()
