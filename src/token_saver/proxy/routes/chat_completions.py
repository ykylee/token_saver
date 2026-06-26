"""POST /v1/chat/completions — OpenAI-compatible chat proxy.

TASK-002-2 shipped a mock response. TASK-002-5-a wires the real
forwarding path:

1. **Parse** the request's ``model`` field — supports the
   ``type/model`` prefix (``openai/gpt-4o-mini``) and the explicit
   ``provider`` field. The two must agree when both are present.
2. **Resolve** through :class:`ProviderRouter` — explicit hint,
   then prefix, then registry default.
3. **Forward** to the resolved provider's ``invoke`` and adapt the
   response so the client sees the OpenAI shape it came in with.

Validation policy:

- ``stream=true`` is still gated (400) — streaming lands in
  TASK-002-6.
- Vendor / network errors are translated into 502 Bad Gateway with
  the same ``ErrorEnvelope`` shape we use everywhere else.
- Empty registry raises 503 — the proxy is healthy but has nothing
  to forward to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from token_saver.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorBody,
    ErrorEnvelope,
)
from token_saver.provider.base import (
    InvokeOptions,
    ProviderConnectionError,
    ProviderResponseError,
)
from token_saver.provider.deps import get_provider_registry
from token_saver.provider.registry import ProviderRegistry
from token_saver.provider.router import (
    AmbiguousProviderError,
    NoProviderAvailableError,
    ProviderRouter,
)

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
    registry: ProviderRegistry = Depends(get_provider_registry),
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
