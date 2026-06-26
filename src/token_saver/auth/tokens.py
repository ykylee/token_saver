"""Session store — bearer-token → user info.

The architecture (docs/architecture.md §4.1) calls for Redis keys
shaped ``session:{token}`` with a 1h TTL. TASK-002-3 ships the
**Protocol** plus an in-memory implementation so tests stay isolated
from a live Redis. A real ``RedisSessionStore`` slots in next to
``InMemorySessionStore`` without touching call sites — same shape,
same TTL semantics, same wire format.

Two rules we hold onto everywhere:

1. **Tokens are opaque**. We never log the token value, never include
   it in traceback frames, and never echo it back beyond the initial
   ``LoginResponse``.
2. **TTL is enforced at write time**. Callers ask for "session for N
   seconds" and the store owns the clock; we don't ask the caller to
   pass ``expires_at`` and hope they computed it correctly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

__all__ = ["SessionStore", "InMemorySessionStore", "SessionPayload"]


@dataclass(frozen=True, slots=True)
class SessionPayload:
    """What the store keeps per token.

    ``expires_at`` is wall-clock seconds; the store compares against
    ``time.time()`` on read so callers don't have to thread a clock
    through every auth dependency.
    """

    user_id: str
    role: str
    expires_at: float


class SessionStore(Protocol):
    """Minimal surface every session store must implement."""

    async def set(self, token: str, payload: SessionPayload) -> None:
        """Persist ``payload`` under ``token`` with the payload's TTL."""
        ...

    async def get(self, token: str) -> SessionPayload | None:
        """Return the payload for ``token`` or ``None`` if missing/expired."""
        ...

    async def revoke(self, token: str) -> None:
        """Drop the session immediately. No-op if already gone."""
        ...


class InMemorySessionStore:
    """Process-local session store, used by tests and as a fallback.

    NOT safe across multiple workers. Real deployments wire a
    Redis-backed impl (TASK-002-5 follow-up) so a user authenticated on
    one worker is recognised by all of them.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, SessionPayload] = {}

    async def set(self, token: str, payload: SessionPayload) -> None:
        self._by_token[token] = payload

    async def get(self, token: str) -> SessionPayload | None:
        payload = self._by_token.get(token)
        if payload is None:
            return None
        if payload.expires_at <= time.time():
            # Lazy eviction — saves callers from having to check expiry
            # in addition to None-vs-present.
            self._by_token.pop(token, None)
            return None
        return payload

    async def revoke(self, token: str) -> None:
        self._by_token.pop(token, None)

    # Test-only helper. Not part of the Protocol — keeps the door open
    # for a Redis impl that can't easily expose a "snapshot" view.
    def __len__(self) -> int:  # pragma: no cover - diagnostic only
        return len(self._by_token)
