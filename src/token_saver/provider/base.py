"""Provider abstraction — Protocol, dataclasses, errors.

The proxy treats every upstream LLM as a :class:`BaseProvider`
regardless of vendor. Each impl owns three concerns:

1. **Health check** — ``test_connection`` probes the configured
   ``base_url`` and reports latency / models count.
2. **Model discovery** — ``list_models`` returns the catalog we
   cached at registration time (and refresh on demand).
3. **Wire-format adaptation** — ``invoke`` translates our
   OpenAI-compatible request into whatever the vendor expects,
   then maps the response back so the caller sees a single shape.

Two impls cover the architecture §2 surface:

- :mod:`token_saver.provider.openai_compat` — OpenAI native +
  Ollama + vLLM (they all speak the OpenAI Chat Completions wire
  format; the only difference is ``base_url``).
- :mod:`token_saver.provider.anthropic` — Anthropic native
  (``POST /v1/messages`` with ``x-api-key``).

Adding a new vendor means a new ``BaseProvider`` impl plus a
:class:`ProviderRegistry.from_config` branch. No call site beyond
the registry knows about the new type.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from token_saver.models import (
    ChatCompletionResponse,
    ChatMessage,
)

__all__ = [
    "BaseProvider",
    "ProviderConfig",
    "ProviderType",
    "ProviderTestResult",
    "ModelInfo",
    "InvokeOptions",
    "ProviderError",
    "ProviderConnectionError",
    "ProviderResponseError",
    "ProviderConfigError",
]


ProviderType = Literal["openai", "anthropic", "ollama", "vllm"]


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Decrypted provider configuration — the in-memory working copy.

    Persistence (Mongo ``providers`` collection, encrypted at rest)
    lives in :mod:`token_saver.provider.store`. By the time a
    :class:`BaseProvider` sees one of these, ``api_key`` and
    ``base_url`` are already decrypted.

    ``id`` is the Mongo ``_id`` (``provider_{24-hex}``) — we carry
    it so audit logs and conversation records can reference the
    provider without re-looking it up.
    """

    id: str
    name: str
    type: ProviderType
    base_url: str
    api_key: str | None
    default_model: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """A single model entry from a provider's ``GET /v1/models``.

    Mirrors OpenAI's ``/v1/models`` ``data[]`` shape; local providers
    (Ollama, vLLM) return the same envelope under their OpenAI-compat
    routes so we don't need a separate dataclass per vendor.
    """

    id: str
    owned_by: str
    context_window: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    """Outcome of ``BaseProvider.test_connection``.

    ``ok=False`` carries an ``error`` string; the route layer
    surfaces it back to the operator verbatim. Latency is in
    milliseconds and rounded — we don't need sub-millisecond
    precision here.
    """

    ok: bool
    latency_ms: int
    models_count: int = 0
    sample_models: tuple[ModelInfo, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InvokeOptions:
    """Per-request knobs forwarded to the provider.

    The chat route extracts these from the OpenAI request shape
    (temperature, top_p, max_tokens, stop, etc.) and passes them
    through. Adding a knob is a two-line change: field here +
    forward in the impl.
    """

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None


@runtime_checkable
class BaseProvider(Protocol):
    """Surface every provider implementation must satisfy.

    Providers are *stateless with respect to the request* — a
    :class:`ProviderConfig` plus a connection pool are enough to
    invoke. That makes them trivial to swap in tests via
    :class:`ProviderRegistry.from_config` + a fixture-supplied
    ``base_url``.
    """

    config: ProviderConfig

    async def test_connection(self) -> ProviderTestResult:
        """Probe the provider's ``GET /v1/models`` (or equivalent).

        Used at registration time (``POST /v1/providers/test``) and
        on health-check intervals (architecture §4.1
        ``provider_health``).
        """
        ...

    async def list_models(self) -> list[ModelInfo]:
        """Return the provider's current model catalog.

        Impls may cache; the registry calls this on TTL expiry and
        on manual ``POST /v1/providers/{id}/models/refresh``.
        """
        ...

    async def invoke(
        self,
        model: str,
        messages: list[ChatMessage],
        options: InvokeOptions,
    ) -> ChatCompletionResponse:
        """Forward a chat-completion request and adapt the response.

        The returned :class:`ChatCompletionResponse` is what the
        client sees; ``routed_provider`` is set by the caller (the
        router) so each impl only deals with its own wire format.
        """
        ...


# ----- Errors -----


class ProviderError(Exception):
    """Base for all provider-originated failures.

    Subclasses map cleanly onto HTTP status codes at the route
    layer: connection → 502, response → 502, config → 500.
    """


class ProviderConnectionError(ProviderError):
    """Network / DNS / TLS failure reaching the upstream."""


class ProviderResponseError(ProviderError):
    """Upstream returned a non-2xx status or malformed body."""


class ProviderConfigError(ProviderError):
    """Local configuration is unusable (e.g. missing api_key for OpenAI)."""


def utc_now_ms() -> int:
    """Wall-clock millis. Rounded; we don't need sub-ms precision."""
    return int(time.time() * 1000)


def unwrap_text_content(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Project a list of ``ChatMessage`` to plain ``{"role","content"}`` dicts.

    OpenAI-compat providers accept strings only for ``content``; we
    reject list-of-parts payloads at the route boundary rather than
    silently dropping vision parts (TASK-002-5-b follow-up).
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m.content, str):
            raise ProviderConfigError(
                "list-of-parts message content is not supported yet "
                "(planned for a follow-up; use plain string content)."
            )
        out.append({"role": m.role, "content": m.content})
    return out


def usage_from_openai_compat(payload: dict[str, Any]) -> dict[str, int]:
    """Extract a usage block from an OpenAI-compat response.

    Some upstreams omit the field on streaming; we default to zero
    so callers can still render an end-of-stream total.
    """
    raw = payload.get("usage") or {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens", 0)),
        "completion_tokens": int(raw.get("completion_tokens", 0)),
        "total_tokens": int(raw.get("total_tokens", 0)),
    }


def usage_from_anthropic(payload: dict[str, Any]) -> dict[str, int]:
    """Extract a usage block from an Anthropic ``messages`` response.

    Anthropic uses ``input_tokens`` / ``output_tokens`` rather than
    OpenAI's ``prompt_tokens`` / ``completion_tokens``.
    """
    raw = payload.get("usage") or {}
    prompt = int(raw.get("input_tokens", 0))
    completion = int(raw.get("output_tokens", 0))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


# Re-export dataclass ``field`` for tests that want to subclass
# the request envelope.
__all__ += ["field"]
