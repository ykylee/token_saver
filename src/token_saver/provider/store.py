"""Provider store — Protocol + in-memory + Mongo impl.

TASK-002-5-a shipped :mod:`token_saver.provider.registry` (an
in-process id → ``BaseProvider`` map). That registry answers two
questions:

1. *Given an id, return the live provider object.* — call site for
   the chat router.
2. *Build a provider from a config.* — operator registration.

It deliberately knows nothing about **persistence**, **multi-tenant
isolation**, or **encryption**. Those are this module's job.

Persistence + tenant isolation surface:

- :meth:`ProviderStore.list` filters by ``user_id`` (regular users see
  their own rows; admins can request ``user_id=None`` to see all).
- :meth:`ProviderStore.create` / :meth:`ProviderStore.update` /
  :meth:`ProviderStore.delete` are the admin / per-user CRUD surface.
  Both wire shapes (``ProviderCreateRequest`` and ``ProviderRecord``)
  cross this boundary — the route layer builds the request, the
  store decides what gets persisted.
- :meth:`ProviderStore.get_for_user` is the chat-router lookup. It
  resolves ``(owner_user_id, provider_id)`` so a regular user can
  never route through another user's provider, even by guessing the
  id. Admins bypass this by passing ``owner_user_id=None``.

Encryption (architecture §4.4):

- ``api_key`` and ``base_url`` are stored encrypted with
  AES-256-GCM (see :mod:`auth.crypto`). The store does the
  encryption on insert and decryption on read — callers see plain
  text in :class:`ProviderRecord`, never cipher blobs.
- The master key is supplied once at construction (the factory in
  :mod:`provider.factory` decodes ``Settings.master_key`` and hands
  the bytes to the store). Key rotation requires app restart — the
  alternative would be per-call key lookup, which defeats the
  purpose of putting it behind a module boundary.

Schema (architecture §4.2 ``providers`` collection):

- ``_id``             ULID-shaped ``provider_{24-hex}``
- ``owner_user_id``   ULID-shaped ``user_{24-hex}``
- ``name``            operator-supplied display name
- ``type``            ``openai`` | ``anthropic`` | ``ollama`` | ``vllm``
- ``base_url_enc``    AES-GCM ciphertext
- ``api_key_enc``     AES-GCM ciphertext (nullable for local LLMs)
- ``default_model``   the model id surfaced to ``/v1/models``
- ``enabled``         admin kill-switch
- ``models_cache``    ``{ models: [...], last_refreshed_at: int }``
- ``created_at``      epoch seconds
- ``updated_at``      epoch seconds (touched on every write)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from token_saver.auth.crypto import (
    decrypt_secret,
    encrypt_secret,
)
from token_saver.models import ProviderRecord

__all__ = [
    "ProviderStore",
    "InMemoryProviderStore",
    "MongoProviderStore",
    "PROVIDERS_COLLECTION",
]


PROVIDERS_COLLECTION = "providers"


@runtime_checkable
class ProviderStore(Protocol):
    """Surface every provider store must implement.

    Multi-tenant isolation is enforced at the store boundary — every
    write attaches ``owner_user_id`` (caller-supplied), every read
    takes an explicit ``user_id`` (or ``None`` for admin's
    cross-tenant view). Routes never reach into the underlying
    collection directly.
    """

    async def list_for_user(
        self, user_id: str | None
    ) -> list[ProviderRecord]: ...
    async def get(
        self, provider_id: str, user_id: str | None
    ) -> ProviderRecord | None: ...
    async def create(
        self,
        *,
        owner_user_id: str,
        name: str,
        type: str,
        base_url: str,
        api_key: str | None,
        default_model: str,
        enabled: bool,
    ) -> ProviderRecord: ...
    async def update(
        self,
        provider_id: str,
        *,
        owner_user_id: str | None,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        enabled: bool | None = None,
    ) -> ProviderRecord | None: ...
    async def delete(
        self, provider_id: str, user_id: str | None
    ) -> bool: ...
    async def set_models_cache(
        self,
        provider_id: str,
        user_id: str | None,
        *,
        models: list[dict[str, object]],
    ) -> ProviderRecord | None: ...
    async def list_enabled_for_user(
        self, user_id: str
    ) -> list[ProviderRecord]: ...


@dataclass(frozen=True, slots=True)
class _MemoryRow:
    """Internal record shape for :class:`InMemoryProviderStore`."""

    id: str
    name: str
    type: str
    base_url: str
    api_key: str | None
    default_model: str
    enabled: bool
    owner_user_id: str
    created_at: int
    models_cache: tuple[dict[str, object], ...] = ()
    models_last_refreshed_at: int | None = None


def _new_provider_id() -> str:
    return f"provider_{uuid.uuid4().hex[:24]}"


def _row_to_record(row: _MemoryRow) -> ProviderRecord:
    return ProviderRecord(
        id=row.id,
        name=row.name,
        type=row.type,  # type: ignore[arg-type]
        base_url=row.base_url,
        api_key=row.api_key,
        default_model=row.default_model,
        enabled=row.enabled,
        owner_user_id=row.owner_user_id,
    )


class InMemoryProviderStore:
    """In-process provider store for tests and the ``memory`` backend.

    Same isolation contract as :class:`MongoProviderStore` — the only
    difference is where the rows live (process dict vs. BSON). Drop-in
    for tests; not safe across multiple workers.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, _MemoryRow] = {}

    async def list_for_user(self, user_id: str | None) -> list[ProviderRecord]:
        rows = [
            r
            for r in self._by_id.values()
            if user_id is None or r.owner_user_id == user_id
        ]
        rows.sort(key=lambda r: r.created_at)
        return [_row_to_record(r) for r in rows]

    async def get(
        self, provider_id: str, user_id: str | None
    ) -> ProviderRecord | None:
        row = self._by_id.get(provider_id)
        if row is None:
            return None
        if user_id is not None and row.owner_user_id != user_id:
            return None
        return _row_to_record(row)

    async def create(
        self,
        *,
        owner_user_id: str,
        name: str,
        type: str,
        base_url: str,
        api_key: str | None,
        default_model: str,
        enabled: bool,
    ) -> ProviderRecord:
        row = _MemoryRow(
            id=_new_provider_id(),
            name=name,
            type=type,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            enabled=enabled,
            owner_user_id=owner_user_id,
            created_at=int(time.time()),
        )
        self._by_id[row.id] = row
        return _row_to_record(row)

    async def update(
        self,
        provider_id: str,
        *,
        owner_user_id: str | None,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        enabled: bool | None = None,
    ) -> ProviderRecord | None:
        row = self._by_id.get(provider_id)
        if row is None:
            return None
        # ``owner_user_id=None`` means "admin, any owner"; a regular
        # user can only touch their own rows. Mirrors the Mongo impl.
        if owner_user_id is not None and row.owner_user_id != owner_user_id:
            return None
        new_row = _MemoryRow(
            id=row.id,
            name=name if name is not None else row.name,
            type=row.type,
            base_url=base_url if base_url is not None else row.base_url,
            api_key=api_key if api_key is not None else row.api_key,
            default_model=default_model if default_model is not None else row.default_model,
            enabled=enabled if enabled is not None else row.enabled,
            owner_user_id=row.owner_user_id,
            created_at=row.created_at,
            models_cache=row.models_cache,
            models_last_refreshed_at=row.models_last_refreshed_at,
        )
        self._by_id[row.id] = new_row
        return _row_to_record(new_row)

    async def delete(self, provider_id: str, user_id: str | None) -> bool:
        row = self._by_id.get(provider_id)
        if row is None:
            return False
        if user_id is not None and row.owner_user_id != user_id:
            return False
        self._by_id.pop(provider_id, None)
        return True

    async def set_models_cache(
        self,
        provider_id: str,
        user_id: str | None,
        *,
        models: list[dict[str, object]],
    ) -> ProviderRecord | None:
        row = self._by_id.get(provider_id)
        if row is None:
            return None
        if user_id is not None and row.owner_user_id != user_id:
            return None
        new_row = _MemoryRow(
            id=row.id,
            name=row.name,
            type=row.type,
            base_url=row.base_url,
            api_key=row.api_key,
            default_model=row.default_model,
            enabled=row.enabled,
            owner_user_id=row.owner_user_id,
            created_at=row.created_at,
            models_cache=tuple(models),
            models_last_refreshed_at=int(time.time()),
        )
        self._by_id[row.id] = new_row
        return _row_to_record(new_row)

    async def list_enabled_for_user(
        self, user_id: str
    ) -> list[ProviderRecord]:
        rows = [
            r
            for r in self._by_id.values()
            if r.owner_user_id == user_id and r.enabled
        ]
        rows.sort(key=lambda r: r.created_at)
        return [_row_to_record(r) for r in rows]


class MongoProviderStore:
    """MongoDB-backed provider store.

    Connection lifecycle is owned by the caller — the factory in
    :mod:`provider.factory` builds the ``AsyncIOMotorClient`` and
    attaches it to ``app.state``. ``create_app`` calls
    :meth:`ensure_indexes` from the FastAPI lifespan startup so
    docker-compose / production gets the schema right without a
    separate migration step.

    The master key is supplied once at construction. Key rotation
    requires app restart — the alternative (per-call key lookup)
    defeats the purpose of putting it behind a module boundary.
    """

    def __init__(
        self,
        client: AsyncIOMotorClient,
        db_name: str,
        *,
        master_key: bytes,
    ) -> None:
        if len(master_key) != 32:
            raise ValueError(
                "master_key must be 32 bytes (AES-256); "
                f"got {len(master_key)}"
            )
        self._client = client
        self._db = client[db_name]
        self._coll: AsyncIOMotorCollection = self._db[PROVIDERS_COLLECTION]
        self._master_key = master_key

    async def ensure_indexes(self) -> None:
        """Create the indexes the store relies on.

        Idempotent — Mongo ``createIndex`` is a no-op if the index
        already exists with the same key spec.
        """
        await self._coll.create_index("owner_user_id")
        await self._coll.create_index([("owner_user_id", 1), ("created_at", 1)])
        await self._coll.create_index("type")

    async def list_for_user(self, user_id: str | None) -> list[ProviderRecord]:
        query: dict[str, object] = {}
        if user_id is not None:
            query["owner_user_id"] = user_id
        cursor = self._coll.find(query).sort("created_at", 1)
        out: list[ProviderRecord] = []
        async for doc in cursor:
            if doc is None:
                continue
            record = _doc_to_record(doc, self._master_key)
            if record is not None:
                out.append(record)
        return out

    async def get(
        self, provider_id: str, user_id: str | None
    ) -> ProviderRecord | None:
        doc = await self._coll.find_one({"_id": provider_id})
        if doc is None:
            return None
        if user_id is not None and doc.get("owner_user_id") != user_id:
            return None
        return _doc_to_record(doc, self._master_key)

    async def create(
        self,
        *,
        owner_user_id: str,
        name: str,
        type: str,
        base_url: str,
        api_key: str | None,
        default_model: str,
        enabled: bool,
    ) -> ProviderRecord:
        now = int(time.time())
        base_url_enc = encrypt_secret(base_url, master_key=self._master_key)
        api_key_enc = (
            encrypt_secret(api_key, master_key=self._master_key)
            if api_key is not None
            else None
        )
        doc = {
            "_id": _new_provider_id(),
            "name": name,
            "type": type,
            "base_url_enc": base_url_enc,
            "api_key_enc": api_key_enc,
            "default_model": default_model,
            "enabled": enabled,
            "owner_user_id": owner_user_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._coll.insert_one(doc)
        stored = await self._coll.find_one({"_id": doc["_id"]})
        record = _doc_to_record(stored, self._master_key)
        assert record is not None  # invariant: we just inserted the doc
        return record

    async def update(
        self,
        provider_id: str,
        *,
        owner_user_id: str | None,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        enabled: bool | None = None,
    ) -> ProviderRecord | None:
        existing = await self._coll.find_one({"_id": provider_id})
        if existing is None:
            return None
        if owner_user_id is not None and existing.get("owner_user_id") != owner_user_id:
            return None
        update: dict[str, object] = {"updated_at": int(time.time())}
        if name is not None:
            update["name"] = name
        if base_url is not None:
            update["base_url_enc"] = encrypt_secret(
                base_url, master_key=self._master_key
            )
        if api_key is not None:
            update["api_key_enc"] = encrypt_secret(
                api_key, master_key=self._master_key
            )
        if default_model is not None:
            update["default_model"] = default_model
        if enabled is not None:
            update["enabled"] = enabled
        await self._coll.update_one({"_id": provider_id}, {"$set": update})
        stored = await self._coll.find_one({"_id": provider_id})
        return _doc_to_record(stored, self._master_key)

    async def delete(self, provider_id: str, user_id: str | None) -> bool:
        existing = await self._coll.find_one({"_id": provider_id})
        if existing is None:
            return False
        if user_id is not None and existing.get("owner_user_id") != user_id:
            return False
        result = await self._coll.delete_one({"_id": provider_id})
        return result.deleted_count == 1

    async def set_models_cache(
        self,
        provider_id: str,
        user_id: str | None,
        *,
        models: list[dict[str, object]],
    ) -> ProviderRecord | None:
        existing = await self._coll.find_one({"_id": provider_id})
        if existing is None:
            return None
        if user_id is not None and existing.get("owner_user_id") != user_id:
            return None
        await self._coll.update_one(
            {"_id": provider_id},
            {
                "$set": {
                    "models_cache": {
                        "models": models,
                        "last_refreshed_at": int(time.time()),
                    },
                    "updated_at": int(time.time()),
                },
            },
        )
        stored = await self._coll.find_one({"_id": provider_id})
        return _doc_to_record(stored, self._master_key)

    async def list_enabled_for_user(
        self, user_id: str
    ) -> list[ProviderRecord]:
        cursor = self._coll.find(
            {"owner_user_id": user_id, "enabled": True}
        ).sort("created_at", 1)
        out: list[ProviderRecord] = []
        async for doc in cursor:
            if doc is None:
                continue
            record = _doc_to_record(doc, self._master_key)
            if record is not None:
                out.append(record)
        return out

    def close(self) -> None:
        """Release the underlying motor client.

        Sync by design — ``AsyncIOMotorClient.close`` cleans up the
        socket pool without awaiting anything on the event loop.
        """
        self._client.close()


def _doc_to_record(
    doc: dict[str, object] | None, master_key: bytes
) -> ProviderRecord | None:
    """Decrypt and translate a Mongo doc into a :class:`ProviderRecord`.

    The encrypted ``base_url`` / ``api_key`` fields are decrypted here
    so callers never see cipher blobs. Decryption failures propagate
    as :class:`token_saver.auth.crypto.InvalidCiphertextError` — the
    alternative (silently returning ``None``) would mask a real
    key-rotation bug.
    """
    if doc is None:
        return None
    base_url_enc = doc.get("base_url_enc")
    if not isinstance(base_url_enc, str):
        raise ValueError("base_url_enc is missing or not a string")
    base_url = decrypt_secret(base_url_enc, master_key=master_key)
    api_key_enc = doc.get("api_key_enc")
    api_key: str | None
    if api_key_enc is None:
        api_key = None
    elif isinstance(api_key_enc, str):
        api_key = decrypt_secret(api_key_enc, master_key=master_key)
    else:
        raise ValueError("api_key_enc is not a string")
    return ProviderRecord(
        id=str(doc["_id"]),
        name=str(doc.get("name", "")),
        type=doc.get("type", "openai"),  # type: ignore[arg-type]
        base_url=base_url,
        api_key=api_key,
        default_model=str(doc.get("default_model", "")),
        enabled=bool(doc.get("enabled", True)),
        owner_user_id=str(doc.get("owner_user_id", "")),
    )
