"""User store — Protocol + in-memory impl.

TASK-002-3 ships an ``InMemoryUserStore`` seeded from
``Settings.admin_email`` / ``Settings.admin_password`` so docker-
compose and ``pytest`` both have a usable identity without standing
up Mongo. TASK-002-4 plugs the MongoDB-backed implementation in
next to this one without changing callers.

Rules:

1. **Passwords are never stored in plain text.** ``add_user`` hashes
   via :func:`auth.crypto.hash_password` before persisting.
2. **Email is the public lookup key.** It's also what the login form
   submits. We don't expose password hashes via any endpoint — the
   store is internal.
3. **Tenant boundary comes later.** Multi-user with the same email
   across tenants is impossible here by construction; the Mongo
   store will own that policy.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from token_saver.auth.crypto import hash_password
from token_saver.config import Settings
from token_saver.models import UserRecord

__all__ = ["UserStore", "InMemoryUserStore"]


class UserStore(Protocol):
    """Minimal surface every user store must implement."""

    async def get_by_email(self, email: str) -> UserRecord | None: ...
    async def get_by_id(self, user_id: str) -> UserRecord | None: ...


@dataclass(frozen=True, slots=True)
class _MemoryRow:
    """Internal record shape — mirrors ``UserRecord`` minus the model
    ceremony so the store stays dict-cheap.
    """

    id: str
    email: str
    password_hash: str
    role: str
    created_at: int


class InMemoryUserStore:
    """In-memory user store seeded from ``Settings`` admin credentials."""

    def __init__(self, settings: Settings) -> None:
        self._by_email: dict[str, _MemoryRow] = {}
        self._by_id: dict[str, _MemoryRow] = {}
        self._seed_admin(settings)

    def _seed_admin(self, settings: Settings) -> None:
        if not settings.admin_email or not settings.admin_password:
            # No credentials configured — leave the store empty. Login
            # attempts will return 401 across the board until the
            # operator seeds a user via the admin CLI (TASK-002-7).
            return
        self._insert(
            email=settings.admin_email,
            password=settings.admin_password,
            role="admin",
        )

    def _insert(self, email: str, password: str, role: str) -> _MemoryRow:
        row = _MemoryRow(
            id=f"user_{uuid.uuid4().hex[:24]}",
            email=email,
            password_hash=hash_password(password),
            role=role,
            created_at=int(time.time()),
        )
        self._by_email[email.lower()] = row
        self._by_id[row.id] = row
        return row

    async def get_by_email(self, email: str) -> UserRecord | None:
        row = self._by_email.get(email.lower())
        return self._to_record(row)

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        row = self._by_id.get(user_id)
        return self._to_record(row)

    @staticmethod
    def _to_record(row: _MemoryRow | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            id=row.id,
            email=row.email,
            password_hash=row.password_hash,
            role=row.role,  # type: ignore[arg-type]
            created_at=row.created_at,
        )

    # Test-only helper for seeding non-admin users mid-test.
    def add_user(self, email: str, password: str, role: str = "user") -> UserRecord:
        row = self._insert(email=email, password=password, role=role)
        record = self._to_record(row)
        assert record is not None  # invariant: insert + read round-trip
        return record
