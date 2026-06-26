"""Provider client + Provider Registry.

Responsibility (architecture.md §2 / §4.2):
- ``base.py`` — ``BaseProvider`` Protocol with ``test_connection`` and
  ``invoke`` methods (OpenAI-compat request/response).
- ``openai.py`` / ``anthropic.py`` / ``ollama.py`` / ``vllm.py`` —
  per-vendor clients. All inherit ``BaseProvider`` so the router
  treats them uniformly.
- ``registry.py`` — ``ProviderRegistry``: registration-time
  ``list_models`` (GET ``{base_url}/v1/models``), Redis models cache
  (TTL 1h), Mongo ``models_cache`` persistence.
- ``cache.py`` — Redis read-through for model lists + health state.

Local LLM providers (Ollama / vLLM) connect to a separate server via
``base_url``; the proxy never hosts the model runtime. Models are
discovered dynamically, not from static config.
"""

from __future__ import annotations
