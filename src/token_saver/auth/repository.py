"""User store — Protocol + in-memory + Mongo impl.

TASK-002-3 shipped the in-memory impl + Protocol. TASK-002-4 adds the
Mongo-backed impl alongside it, plus an ``upsert`` method that both
impls share so :func:`auth.seed.seed_admin` is backend-agnostic.

Schema (architecture §4.2):
- ``_id``         ULID-shaped ``user_{24-hex}``
- ``email``       unique, lower-cased before storage
- ``password_hash`` argon2id result of the user's password
- ``role``        ``admin`` | ``user``
- ``created_at``  epoch seconds (set on insert, immutable)
- ``updated_at``  epoch seconds (touched on every upsert)

``UserStore`` is the read surface; ``upsert`` is the admin surface
(used by seed and by the future ``admin create-user`` CLI). New
operations should be added as separate Protocol methods so the read
surface stays small.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from token_saver.auth.crypto import hash_password
from token_saver.config import Settings
from token_saver.models import UserRecord

__all__ = [
    "UserStore",
    "InMemoryUserStore",
    "MongoUserStore",
    "USERS_COLLECTION",
]


USERS_COLLECTION = "users"


@runtime_checkable
class UserStore(Protocol):
    """Surface every user store must implement.

    ``upsert`` is the admin path — it both creates and updates, and
    callers (seed scripts, CLI) own the password policy. Read
    surfaces (``get_by_email`` / ``get_by_id``) MUST NOT expose the
    password hash beyond what the store returns internally.
    """

    async def get_by_email(self, email: str) -> UserRecord | None: ...
    async def get_by_id(self, user_id: str) -> UserRecord | None: ...
    async def upsert(
        self, email: str, password: str, role: str
    ) -> UserRecord: ...


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


def _new_user_id() -> str:
    return f"user_{uuid.uuid4().hex[:24]}"


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


class InMemoryUserStore:
    """In-memory user store seeded from ``Settings`` admin credentials.

    Drop-in for tests and as a fallback when ``Settings.user_store_backend``
    is ``memory``. Not safe across multiple workers.
    """

    def __init__(self, settings: Settings) -> None:
        self._by_email: dict[str, _MemoryRow] = {}
        self._by_id: dict[str, _MemoryRow] = {}
        self._seed_admin(settings)

    def _seed_admin(self, settings: Settings) -> None:
        if not settings.admin_email or not settings.admin_password:
            return
        # Idempotent — only seeds if missing. Re-running the seed won't
        # overwrite an existing admin's password.
        if settings.admin_email.lower() in self._by_email:
            return
        self._insert(
            email=settings.admin_email,
            password=settings.admin_password,
            role="admin",
        )

    def _insert(self, email: str, password: str, role: str) -> _MemoryRow:
        row = _MemoryRow(
            id=_new_user_id(),
            email=email.lower(),
            password_hash=hash_password(password),
            role=role,
            created_at=int(time.time()),
        )
        self._by_email[row.email] = row
        self._by_id[row.id] = row
        return row

    async def get_by_email(self, email: str) -> UserRecord | None:
        return _to_record(self._by_email.get(email.lower()))

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        return _to_record(self._by_id.get(user_id))

    async def upsert(self, email: str, password: str, role: str) -> UserRecord:
        existing = self._by_email.get(email.lower())
        if existing is not None:
            row = _MemoryRow(
                id=existing.id,
                email=existing.email,
                password_hash=hash_password(password),
                role=role,
                created_at=existing.created_at,
            )
            self._by_email[row.email] = row
            self._by_id[row.id] = row
        else:
            row = self._insert(email=email, password=password, role=role)
        record = _to_record(row)
        assert record is not None  # invariant: insert + read round-trip
        return record

    # Test-only helper. Not part of the Protocol — kept for ergonomic
    # fixture setup; production code should call ``upsert`` instead.
    def add_user(  # pragma: no cover - convenience wrapper
        self, email: str, password: str, role: str = "user"
    ) -> UserRecord:
        row = self._insert(email=email, password=password, role=role)
        record = _to_record(row)
        assert record is not None
        return record


class MongoUserStore:
    """MongoDB-backed user store.

    Connection lifecycle is owned by the caller: the factory in
    :mod:`auth.factory` builds the ``AsyncIOMotorClient``, attaches it
    to ``app.state``, and ``proxy.app.create_app`` calls
    :meth:`ensure_indexes` from the FastAPI lifespan startup hook.
    """

    def __init__(self, client: AsyncIOMotorClient, db_name: str) -> None:
        self._client = client
        self._db = client[db_name]
        self._coll: AsyncIOMotorCollection = self._db[USERS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Create the indexes the store relies on.

        Idempotent — Mongo ``createIndex`` is a no-op if the index
        already exists with the same key spec. Called from the FastAPI
        lifespan startup so docker-compose / production gets the
        schema right without a separate migration step.
        """
        await self._coll.create_index("email", unique=True)
        await self._coll.create_index("role")
        await self._coll.create_index("created_at")

    async def get_by_email(self, email: str) -> UserRecord | None:
        doc = await self._coll.find_one({"email": email.lower()})
        return _doc_to_record(doc)

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        doc = await self._coll.find_one({"_id": user_id})
        return _doc_to_record(doc)

    async def upsert(self, email: str, password: str, role: str) -> UserRecord:
        """Insert or update a user, hashing the password on the way in.

        ``created_at`` is preserved across updates (we never touch it on
        the update path); ``updated_at`` reflects the latest write.
        ``_id`` is preserved across updates so references to the user
        (sessions, audit log) stay stable across password rotation.
        """
        now = int(time.time())
        existing = await self.get_by_email(email)
        if existing is None:
            doc = {
                "_id": _new_user_id(),
                "email": email.lower(),
                "password_hash": hash_password(password),
                "role": role,
                "created_at": now,
                "updated_at": now,
            }
            await self._coll.insert_one(doc)
        else:
            await self._coll.update_one(
                {"_id": existing.id},
                {
                    "$set": {
                        "password_hash": hash_password(password),
                        "role": role,
                        "updated_at": now,
                    },
                },
            )
        stored = await self._coll.find_one({"email": email.lower()})
        record = _doc_to_record(stored)
        assert record is not None  # invariant: we just wrote the doc
        return record

    def close(self) -> None:
        """Release the underlying motor client.

        Sync by design — ``AsyncIOMotorClient.close`` cleans up the
        socket pool without awaiting anything on the event loop.
        """
        self._client.close()


def _doc_to_record(doc: dict[str, object] | None) -> UserRecord | None:
    if doc is None:
        return None
    return UserRecord(
        id=str(doc["_id"]),
        email=str(doc["email"]),
        password_hash=str(doc["password_hash"]),
        role=doc["role"],  # type: ignore[arg-type]
        created_at=int(doc["created_at"]),  # type: ignore[call-overload]
    )
