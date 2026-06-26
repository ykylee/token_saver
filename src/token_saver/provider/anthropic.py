"""Anthropic provider — native ``POST /v1/messages`` wire format.

Anthropic deviates from OpenAI's wire in two material ways:

1. **Headers** — ``x-api-key`` (not ``Authorization: Bearer``) plus
   the mandatory ``anthropic-version: 2023-06-01``.
2. **Request body** — ``max_tokens`` is **required**; ``system``
   messages must be lifted out of the ``messages`` array into a
   top-level ``system`` field.
3. **Response body** — content is a list of typed blocks
   (``[{"type": "text", "text": "..."}]``) and the token-usage
   field uses ``input_tokens`` / ``output_tokens`` rather than
   ``prompt_tokens`` / ``completion_tokens``.

We adapt both directions so the proxy's clients keep seeing the
OpenAI shape they came in with.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from token_saver.models import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatMessage,
    Usage,
)
from token_saver.provider.base import (
    InvokeOptions,
    ModelInfo,
    ProviderConfig,
    ProviderConfigError,
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTestResult,
    usage_from_anthropic,
    utc_now_ms,
)

__all__ = ["AnthropicProvider", "ANTHROPIC_API_VERSION"]


ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider:
    """Anthropic-native impl behind ``BaseProvider``-shaped interface."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.config = config
        if not config.api_key:
            # Anthropic rejects every request without ``x-api-key``,
            # so failing here is better than surfacing 401s forever.
            raise ProviderConfigError(
                "anthropic provider requires api_key (x-api-key header)"
            )
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key or "",
            "anthropic-version": ANTHROPIC_API_VERSION,
        }

    async def test_connection(self) -> ProviderTestResult:  # noqa: F821
        """Anthropic doesn't expose ``GET /v1/models``; send a 1-token
        completion as a liveness probe instead.

        Capped at 1 max_token so the operator who hits "Test" doesn't
        accidentally spend credits. Errors map onto ``ProviderTestResult``
        so the route layer's error envelope stays uniform.
        """
        start = utc_now_ms()
        try:
            resp = await self._client.post(
                f"{self.config.base_url.rstrip('/')}/v1/messages",
                headers=self._headers,
                json={
                    "model": self.config.default_model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
        except httpx.HTTPError as exc:
            return ProviderTestResult(
                ok=False,
                latency_ms=utc_now_ms() - start,
                error=f"connection error: {exc.__class__.__name__}: {exc}",
            )
        latency = utc_now_ms() - start
        if resp.status_code not in (200, 400):
            # 400 is acceptable: auth + model reachability both
            # validated; only network / 5xx are hard fails.
            return ProviderTestResult(
                ok=False,
                latency_ms=latency,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ProviderTestResult(
            ok=True,
            latency_ms=latency,
            models_count=1,
            sample_models=(ModelInfo(id=self.config.default_model, owned_by="anthropic"),),
        )

    async def list_models(self) -> list[ModelInfo]:
        """Anthropic has no model list endpoint; fall back to the default.

        The registry's ``default_model`` is the source of truth at
        registration time. We synthesise a one-element catalog so
        :meth:`ProviderRegistry.list_models` keeps a uniform shape
        across vendors.
        """
        return [ModelInfo(id=self.config.default_model, owned_by="anthropic")]

    async def invoke(
        self,
        model: str,
        messages: list[ChatMessage],
        options: InvokeOptions,
    ) -> ChatCompletionResponse:
        """Adapt the OpenAI-style request into Anthropic's native shape.

        Two structural transforms:

        - Pull the system message(s) out of ``messages`` into the
          top-level ``system`` field (Anthropic forbids them inside
          ``messages``).
        - Default ``max_tokens`` if the caller didn't supply one —
          Anthropic rejects requests without it.
        """
        anthropic_messages, system_blocks = _split_system(messages)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": options.max_tokens or 1024,
            "messages": anthropic_messages,
        }
        if system_blocks:
            body["system"] = "\n\n".join(system_blocks)
        if options.temperature is not None:
            body["temperature"] = options.temperature
        if options.top_p is not None:
            body["top_p"] = options.top_p
        if options.stop is not None:
            body["stop_sequences"] = (
                [options.stop] if isinstance(options.stop, str) else list(options.stop)
            )

        try:
            resp = await self._client.post(
                f"{self.config.base_url.rstrip('/')}/v1/messages",
                headers=self._headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"POST /v1/messages failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ProviderResponseError(
                f"POST /v1/messages returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        payload = resp.json()
        text = _extract_text(payload.get("content") or [])
        finish_reason = _anthropic_finish_reason(payload.get("stop_reason"))

        usage = Usage(**usage_from_anthropic(payload))
        return ChatCompletionResponse(
            id=str(payload.get("id", f"chatcmpl-{int(time.time() * 1000)}")),
            created=int(time.time()),
            model=str(payload.get("model", model)),
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason=finish_reason,  # type: ignore[arg-type]
                )
            ],
            usage=usage,
            routed_provider=None,
        )


def _split_system(messages: list[ChatMessage]) -> tuple[list[dict[str, Any]], list[str]]:
    """Lift ``system`` messages out into Anthropic's top-level slot."""
    anthropic_messages: list[dict[str, Any]] = []
    system_blocks: list[str] = []
    for m in messages:
        if not isinstance(m.content, str):
            raise ProviderConfigError(
                "list-of-parts message content is not supported yet "
                "(planned for a follow-up; use plain string content)."
            )
        if m.role == "system":
            system_blocks.append(m.content)
        else:
            anthropic_messages.append({"role": m.role, "content": m.content})
    return anthropic_messages, system_blocks


def _extract_text(content: list[dict[str, Any]]) -> str:
    """Concatenate the ``text`` blocks of an Anthropic response.

    Tool-use blocks (which Anthropic represents as ``type: tool_use``)
    are ignored for now — the proxy's OpenAI-compat contract only
    emits text. Tool-calling lands in a follow-up.
    """
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _anthropic_finish_reason(stop_reason: str | None) -> str:
    """Map Anthropic's stop_reason onto OpenAI's finish_reason vocabulary.

    ``end_turn`` → ``stop`` (the common case); ``max_tokens`` →
    ``length``; ``tool_use`` → ``tool_calls``; ``stop_sequence`` →
    ``stop``; anything else → ``stop`` (best-effort).
    """
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "stop_sequence": "stop",
    }
    return mapping.get(stop_reason or "", "stop")
