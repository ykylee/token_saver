"""Provider registry — in-memory map of ``provider_id`` → ``BaseProvider``.

The registry owns three responsibilities and nothing more:

1. **Lookup** by id (used by the chat router).
2. **Construction** via :meth:`ProviderRegistry.from_config` — single
   place that knows the type → impl mapping.
3. **Lifecycle** for the registered providers (closing clients on
   shutdown).

Multi-user isolation lives one layer up in the persistence-backed
``ProviderStore`` (TASK-002-5-b). For now this registry holds a
single flat map; tests scope it via fresh instances.
"""

from __future__ import annotations

from collections.abc import Iterable

from token_saver.provider.anthropic import AnthropicProvider
from token_saver.provider.base import BaseProvider, ProviderConfig, ProviderType
from token_saver.provider.openai_compat import OpenAICompatProvider

__all__ = ["ProviderRegistry", "UnknownProviderTypeError"]


class UnknownProviderTypeError(ValueError):
    """Raised when ``from_config`` is asked for a vendor we don't speak.

    Surfaces at registration time so the operator sees the typo
    immediately, not as a confusing 502 at first request.
    """


class ProviderRegistry:
    """Flat id → provider map.

    Tenant isolation, persistence, and cache freshness are out of
    scope here — see ``provider.store`` (TASK-002-5-b).
    """

    def __init__(self) -> None:
        self._by_id: dict[str, BaseProvider] = {}

    # ----- registration -----

    def register(self, provider: BaseProvider) -> None:
        """Add (or replace) a provider by its config id."""
        self._by_id[provider.config.id] = provider

    def unregister(self, provider_id: str) -> None:
        """Drop a provider; no-op if it isn't registered.

        Also closes the underlying HTTP client so we don't leak
        sockets. After this returns the provider must NOT be used.
        """
        provider = self._by_id.pop(provider_id, None)
        if provider is None:
            return
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:  # pragma: no cover - defensive
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(aclose())
                else:
                    loop.run_until_complete(aclose())
            except RuntimeError:
                # No loop in this thread — caller's responsibility.
                pass

    def get(self, provider_id: str) -> BaseProvider | None:
        """Lookup by id; returns ``None`` if not registered."""
        return self._by_id.get(provider_id)

    def find_by_type(self, provider_type: str) -> BaseProvider | None:
        """First registered provider with ``config.type == provider_type``.

        Used by the chat router when a request specifies a
        ``provider/`` model prefix or a ``provider`` field. Returns
        the first match in insertion order — multi-tenant scoping
        (TASK-002-5-b) is the layer that owns "which provider
        belongs to whom".
        """
        for provider in self._by_id.values():
            if provider.config.type == provider_type:
                return provider
        return None

    def find_default(self) -> BaseProvider | None:
        """Return the first registered provider, or ``None``.

        Used when a request omits both ``model`` prefix and
        ``provider`` field. Deterministic for now (insertion
        order) — a more sophisticated default policy lands when
        per-user routing arrives in TASK-002-5-b.
        """
        providers = list(self._by_id.values())
        return providers[0] if providers else None

    def all(self) -> Iterable[BaseProvider]:
        """Snapshot of registered providers (in insertion order)."""
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    async def aclose(self) -> None:
        """Close every owned HTTP client on app shutdown."""
        for provider in list(self._by_id.values()):
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    # ----- factory -----

    @staticmethod
    def from_config(config: ProviderConfig) -> BaseProvider:
        """Pick an impl based on ``ProviderConfig.type``.

        OpenAI / Ollama / vLLM collapse into :class:`OpenAICompatProvider`
        because all three speak the same wire format — the only
        difference is ``base_url`` and whether the operator runs
        an API key (Ollama / vLLM usually skip it).
        """
        provider_type: ProviderType = config.type
        if provider_type in ("openai", "ollama", "vllm"):
            return OpenAICompatProvider(config)
        if provider_type == "anthropic":
            return AnthropicProvider(config)
        raise UnknownProviderTypeError(
            f"unknown provider type {provider_type!r}; "
            "expected one of: openai, anthropic, ollama, vllm"
        )
