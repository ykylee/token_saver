"""OpenAI-compatible provider — covers OpenAI, Ollama, vLLM.

All three expose the same wire shape:

- ``GET  {base_url}/v1/models``        → ``{"object": "list", "data": [...]}``
- ``POST {base_url}/v1/chat/completions`` → OpenAI ChatCompletion schema

We share the same client across all three; the only differences are
``base_url`` and the optional ``Authorization`` header. Local
inference servers (Ollama, vLLM) usually run without a key, so
``api_key`` is best-effort.

The wire format and the cache format are intentionally the same —
``list_models`` writes straight into the cache layer without an
intermediate dict conversion.
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
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTestResult,
    unwrap_text_content,
    usage_from_openai_compat,
    utc_now_ms,
)

__all__ = ["OpenAICompatProvider"]


class OpenAICompatProvider:
    """Single impl for OpenAI native + Ollama + vLLM OpenAI-compat endpoints.

    The class is *not* generic across the three vendor names — it
    just so happens to speak the OpenAI Chat Completions wire
    format, which all three vendors adopted. ``ProviderConfig.type``
    is preserved end-to-end so audit logs and per-vendor analytics
    can distinguish them without re-deriving from ``base_url``.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.config = config
        # Tests inject a stub client so they can pin status codes /
        # bodies. Production gets a fresh client per provider so the
        # connection pool lines up with the config's lifecycle.
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def test_connection(self) -> ProviderTestResult:
        """Hit ``GET /v1/models`` to confirm reachability + auth.

        On 200, we also report latency and the discovered count so
        the operator gets a single-shot verdict at registration
        time.
        """
        start = utc_now_ms()
        try:
            resp = await self._client.get(
                f"{self.config.base_url.rstrip('/')}/v1/models",
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            return ProviderTestResult(
                ok=False,
                latency_ms=utc_now_ms() - start,
                error=f"connection error: {exc.__class__.__name__}: {exc}",
            )

        latency = utc_now_ms() - start
        if resp.status_code != 200:
            return ProviderTestResult(
                ok=False,
                latency_ms=latency,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        body = resp.json()
        data = body.get("data") or []
        sample = tuple(
            ModelInfo(
                id=str(item.get("id", "")),
                owned_by=str(item.get("owned_by", self.config.type)),
            )
            for item in data[:5]
            if item.get("id")
        )
        return ProviderTestResult(
            ok=True,
            latency_ms=latency,
            models_count=len(data),
            sample_models=sample,
        )

    async def list_models(self) -> list[ModelInfo]:
        """Read the full catalog (caller caches it).

        Raises :class:`ProviderResponseError` on non-200 so the
        caller can distinguish "upstream is down" from "catalog is
        empty" — the latter is benign.
        """
        try:
            resp = await self._client.get(
                f"{self.config.base_url.rstrip('/')}/v1/models",
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"GET /v1/models failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ProviderResponseError(
                f"GET /v1/models returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        return [
            ModelInfo(
                id=str(item.get("id", "")),
                owned_by=str(item.get("owned_by", self.config.type)),
            )
            for item in (body.get("data") or [])
            if item.get("id")
        ]

    async def invoke(
        self,
        model: str,
        messages: list[ChatMessage],
        options: InvokeOptions,
    ) -> ChatCompletionResponse:
        """Forward to ``POST /v1/chat/completions`` and adapt the response.

        We **never** inject ``X-Token-Saver-Mock`` here; the
        :class:`routes.chat_completions` route used to add that
        header in mock mode but real forwarding is signalled by its
        absence.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": unwrap_text_content(messages),
        }
        if options.temperature is not None:
            body["temperature"] = options.temperature
        if options.top_p is not None:
            body["top_p"] = options.top_p
        if options.max_tokens is not None:
            body["max_tokens"] = options.max_tokens
        if options.stop is not None:
            body["stop"] = options.stop
        if options.presence_penalty is not None:
            body["presence_penalty"] = options.presence_penalty
        if options.frequency_penalty is not None:
            body["frequency_penalty"] = options.frequency_penalty
        if options.user is not None:
            body["user"] = options.user

        try:
            resp = await self._client.post(
                f"{self.config.base_url.rstrip('/')}/v1/chat/completions",
                headers=self._headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"POST /v1/chat/completions failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ProviderResponseError(
                f"POST /v1/chat/completions returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        payload = resp.json()
        choices_raw = payload.get("choices") or []
        if not choices_raw:
            raise ProviderResponseError(
                "POST /v1/chat/completions returned no choices"
            )
        choices = [
            ChatCompletionChoice(
                index=int(c.get("index", i)),
                message=ChatMessage(
                    role=c.get("message", {}).get("role", "assistant"),
                    content=c.get("message", {}).get("content", ""),
                ),
                finish_reason=c.get("finish_reason", "stop"),
            )
            for i, c in enumerate(choices_raw)
        ]
        usage = Usage(**usage_from_openai_compat(payload))

        return ChatCompletionResponse(
            id=str(payload.get("id", f"chatcmpl-{int(time.time() * 1000)}")),
            created=int(payload.get("created", time.time())),
            model=str(payload.get("model", model)),
            choices=choices,
            usage=usage,
            routed_provider=None,  # router fills this in
        )
