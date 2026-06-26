"""Provider router — parse a request, find a provider, hand off.

The router is a thin layer over :class:`ProviderRegistry` that owns
two policies:

1. **Prefix parsing** — ``openai/gpt-4o-mini`` →
   ``provider_type="openai", model="gpt-4o-mini"``. Plain
   ``gpt-4o-mini`` (no slash) leaves the provider unspecified and
   defers to the registry's default.
2. **Resolution** — explicit ``provider`` field on the request wins;
   prefix from the model string is the fallback; no signal at all
   means "use the registry default".

Multi-user isolation (which providers the caller is *allowed* to
route to) is one layer up — TASK-002-5-b adds the
``ProviderStore`` that returns a per-user slice of the registry.
For TASK-002-5-a we trust the registry as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from token_saver.models import ChatCompletionResponse, ChatMessage
from token_saver.provider.base import (
    BaseProvider,
    InvokeOptions,
    ProviderError,
)

if TYPE_CHECKING:
    from token_saver.provider.registry import ProviderRegistry

__all__ = [
    "ProviderRouter",
    "RouteResult",
    "NoProviderAvailableError",
    "AmbiguousProviderError",
]


class NoProviderAvailableError(ProviderError):
    """Raised when no registered provider matches the request.

    Maps to HTTP 503 at the route layer — the proxy is healthy but
    has no upstream to talk to.
    """


class AmbiguousProviderError(ProviderError):
    """Raised when a provider hint conflicts with the model prefix.

    Maps to HTTP 400 — the caller gave us contradictory routing
    signals and we refuse to guess.
    """


@dataclass(frozen=True, slots=True)
class RouteResult:
    """The router's answer to a routing query."""

    provider: BaseProvider
    model: str


class ProviderRouter:
    """Stateless router over a :class:`ProviderRegistry`.

    Built once at app startup and shared across requests. The
    router itself owns no per-request state — only the parsed
    :class:`RouteResult` survives a single call.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def route(
        self,
        model_spec: str,
        provider_hint: str | None = None,
    ) -> RouteResult:
        """Resolve a model spec to a (provider, model) pair.

        ``provider_hint`` is the explicit ``provider`` field on the
        request; ``model_spec`` may carry a ``type/model`` prefix.
        When both are present and agree, fine. When they conflict,
        we raise :class:`AmbiguousProviderError`.
        """
        prefix, model = _parse_model_prefix(model_spec)

        if provider_hint is not None and prefix is not None and provider_hint != prefix:
            raise AmbiguousProviderError(
                f"provider hint {provider_hint!r} conflicts with model prefix {prefix!r}"
            )

        provider_type = provider_hint or prefix
        if provider_type is not None:
            provider = self._registry.find_by_type(provider_type)
        else:
            provider = self._registry.find_default()

        if provider is None:
            raise NoProviderAvailableError(
                f"no registered provider matches "
                f"provider_hint={provider_hint!r} prefix={prefix!r}"
            )
        return RouteResult(provider=provider, model=model)

    async def invoke(
        self,
        model_spec: str,
        messages: list[ChatMessage],
        options: InvokeOptions,
        provider_hint: str | None = None,
    ) -> tuple[BaseProvider, ChatCompletionResponse]:  # type: ignore[name-defined]
        """Route + invoke in one call. Returns ``(provider, response)``.

        Re-raising :class:`ProviderConnectionError` /
        :class:`ProviderResponseError` is intentional — the route
        layer translates them into 502s so the caller gets a single
        uniform error envelope regardless of vendor.
        """
        result = self.route(model_spec, provider_hint=provider_hint)
        response = await result.provider.invoke(result.model, messages, options)
        return result.provider, response


def _parse_model_prefix(model_spec: str) -> tuple[str | None, str]:
    """Split ``type/model`` into ``(type, model)``.

    Splits on the **first** ``/`` only, so a model id that happens
    to contain a slash (none in practice, but defensive) survives
    intact. A bare ``model`` returns ``(None, model)``.
    """
    if "/" not in model_spec:
        return None, model_spec
    prefix, rest = model_spec.split("/", 1)
    prefix = prefix.strip()
    rest = rest.strip()
    if not prefix or not rest:
        return None, model_spec
    return prefix, rest
