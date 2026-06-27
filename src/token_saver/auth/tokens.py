"""Session store — bearer-token → user info.

The architecture (docs/architecture.md §4.1) calls for Redis keys
shaped ``session:{token}`` with a 1h TTL. Three impls:

- :class:`InMemorySessionStore` — process-local. Used by tests and as
  the default fallback. NOT safe across multiple workers.
- :class:`RedisSessionStore` — production. Same Protocol surface,
  talks to ``redis.asyncio.Redis``.

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

from redis.asyncio import Redis

__all__ = ["SessionStore", "InMemorySessionStore", "RedisSessionStore", "SessionPayload"]


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
    Redis-backed impl so a user authenticated on one worker is
    recognised by all of them.
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


class RedisSessionStore:
    """Redis-backed session store.

    Key shape: ``session:{token}`` (matches architecture §4.1). The
    value is a JSON-encoded :class:`SessionPayload`. TTL is set via
    Redis ``EX`` so Redis itself evicts expired keys — no lazy
    eviction needed because Redis does it for us.

    Connection lifecycle is owned by the caller: the factory in
    :mod:`provider.factory` builds the ``redis.asyncio.Redis``
    instance and the FastAPI lifespan closes it on shutdown.
    """

    def __init__(
        self, client: Redis, *, key_prefix: str = "session:", ttl_seconds: int = 3600
    ) -> None:
        self._client = client
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    @staticmethod
    def _serialize(payload: SessionPayload) -> str:
        # Hand-rolled JSON keeps the dependency surface small — the
        # alternative (``json.dumps(dataclasses.asdict(payload))``)
        # works too but adds an import.
        import json

        return json.dumps(
            {
                "user_id": payload.user_id,
                "role": payload.role,
                "expires_at": payload.expires_at,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize(raw: str) -> SessionPayload | None:
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        user_id = data.get("user_id")
        role = data.get("role")
        expires_at = data.get("expires_at")
        if not isinstance(user_id, str) or not isinstance(role, str):
            return None
        if not isinstance(expires_at, (int, float)):
            return None
        return SessionPayload(
            user_id=user_id, role=role, expires_at=float(expires_at)
        )

    async def set(self, token: str, payload: SessionPayload) -> None:
        ttl = max(int(payload.expires_at - time.time()), 1)
        await self._client.set(self._key(token), self._serialize(payload), ex=ttl)

    async def get(self, token: str) -> SessionPayload | None:
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = self._deserialize(raw)
        if payload is None:
            return None
        if payload.expires_at <= time.time():
            # Belt-and-braces: Redis TTL should already have evicted,
            # but a manual ``EXPIRE`` race could leave a stale entry.
            await self._client.delete(self._key(token))
            return None
        return payload

    async def revoke(self, token: str) -> None:
        await self._client.delete(self._key(token))

    async def aclose(self) -> None:
        """Close the underlying Redis client.

        Caller (factory) passes a dedicated client per store so
        shutdown is clean.
        """
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            return None
