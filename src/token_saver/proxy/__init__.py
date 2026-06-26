"""HTTP proxy: FastAPI app + OpenAI-compatible endpoints.

Responsibility (architecture.md §2):
- Mount ``/v1/chat/completions`` (OpenAI-compatible request shape).
- Mount ``/v1/models`` (provider-aware model listing).
- Mount ``/v1/ccr/retrieve`` (CCR-lite direct lookup).
- Mount ``/admin/health`` and ``/admin/audit`` (operator surface).
- Wire auth middleware, rate-limit dependency, and request lifecycle.

Status (TASK-002-1 skeleton): app factory and route stubs only.
The behaviour lands in TASK-002-2 (mock response) → TASK-002-7 (CLI + e2e).
"""

from __future__ import annotations

from token_saver.proxy.app import create_app

__all__ = ["create_app"]
