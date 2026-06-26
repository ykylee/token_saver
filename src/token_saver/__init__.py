"""Token Saver Router.

OpenAI-compatible HTTP proxy with policy-driven content type handling,
pluggable compression, and CCR-lite caching. See ``docs/architecture.md``
for the system design and ``docs/concepts/token-saver-concept.md`` for the
rationale / reference comparisons.

Package layout
--------------
- :mod:`token_saver.proxy` — FastAPI app + OpenAI-compatible endpoints.
- :mod:`token_saver.auth` — Bearer token middleware + RBAC.
- :mod:`token_saver.ratelimit` — Redis sliding window rate limiter.
- :mod:`token_saver.detector` — Content type classifier (text/json/code/log).
- :mod:`token_saver.compressor` — Pluggable compressor registry.
- :mod:`token_saver.provider` — Provider client Protocol + Provider Registry.
- :mod:`token_saver.ccr` — CCR-lite (Redis read-through + Mongo store).
- :mod:`token_saver.cli` — ``token-saver`` console entry point.

This skeleton (TASK-002-1) ships module placeholders only; the public surface
is stable, the behaviour lands across TASK-002-2 .. TASK-002-7.
"""

from __future__ import annotations

from token_saver.__version__ import __version__

__all__ = ["__version__"]
