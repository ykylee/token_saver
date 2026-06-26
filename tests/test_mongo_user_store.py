"""Behavioural tests for :class:`auth.repository.MongoUserStore`.

These run against ``mongomock-motor`` — an in-process async MongoDB
mock — so we exercise the actual motor API surface (collections,
indexes, upserts) without standing up a real container. The wire
format and operations match a real MongoDB instance 1:1 for our
use cases (find / insert / update_one / create_index).

Caveat: mongomock-motor doesn't perfectly mirror Mongo's query
planner, so tests stay focused on observable semantics rather than
performance characteristics.
"""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

from token_saver.auth.repository import USERS_COLLECTION, MongoUserStore


@pytest.fixture
async def store() -> MongoUserStore:
    """Fresh in-memory Mongo + the production wrapper around it."""
    client = AsyncMongoMockClient()
    s = MongoUserStore(client=client, db_name="token_saver_test")
    yield s
    s.close()


async def test_ensure_indexes_is_idempotent(store: MongoUserStore) -> None:
    """``createIndex`` no-ops if the index exists with the same key spec."""
    await store.ensure_indexes()
    await store.ensure_indexes()  # second call must not raise

    # Confirm the indexes exist by listing them through the mock client.
    client = store._client  # noqa: SLF001 — test-only inspection
    info = await client["token_saver_test"][USERS_COLLECTION].index_information()
    assert "email_1" in info
    assert info["email_1"].get("unique") is True
    assert "role_1" in info
    assert "created_at_1" in info


async def test_upsert_creates_new_user(store: MongoUserStore) -> None:
    """First upsert inserts; second lookup returns the same record."""
    record = await store.upsert(
        email="Alice@Example.com", password="hunter2", role="admin"
    )
    assert record.id.startswith("user_")
    assert record.email == "alice@example.com"  # lower-cased on storage
    assert record.role == "admin"
    assert record.password_hash  # non-empty

    found = await store.get_by_email("alice@example.com")
    assert found is not None
    assert found.id == record.id


async def test_upsert_updates_existing_user_and_preserves_created_at(
    store: MongoUserStore,
) -> None:
    """Re-upsert keeps the same ``_id`` and original ``created_at``."""
    first = await store.upsert(
        email="bob@example.com", password="old-pw", role="user"
    )
    second = await store.upsert(
        email="bob@example.com", password="new-pw", role="admin"
    )
    assert second.id == first.id
    assert second.created_at == first.created_at  # $setOnInsert honoured
    assert second.role == "admin"  # role updated

    # Login-shape check: the new password verifies, the old one doesn't.
    from token_saver.auth.crypto import InvalidCredentialsError, verify_password

    verify_password("new-pw", second.password_hash)  # raises on fail
    with pytest.raises(InvalidCredentialsError):
        verify_password("old-pw", second.password_hash)


async def test_get_by_email_returns_none_for_unknown(store: MongoUserStore) -> None:
    """Missing email is ``None``, not a raised exception."""
    assert await store.get_by_email("nobody@example.com") is None


async def test_get_by_id_returns_none_for_unknown(store: MongoUserStore) -> None:
    """Missing id is ``None``, not a raised exception."""
    assert await store.get_by_id("user_doesnotexist") is None


async def test_get_by_email_is_case_insensitive(store: MongoUserStore) -> None:
    """Email storage is lower-cased; reads should match any case."""
    await store.upsert(
        email="Mixed@Example.com", password="pw", role="user"
    )
    assert await store.get_by_email("mixed@example.com") is not None
    assert await store.get_by_email("MIXED@example.com") is not None
    assert await store.get_by_email("MiXeD@example.com") is not None


async def test_unique_email_index_rejects_duplicates(store: MongoUserStore) -> None:
    """Direct collection insert of a duplicate email must fail.

    Bypasses ``upsert`` (which uses update_one) and tests the index
    contract that protects against race conditions in two concurrent
    seeders.
    """
    from pymongo.errors import DuplicateKeyError

    await store.ensure_indexes()
    coll = store._coll  # noqa: SLF001
    await coll.insert_one(
        {
            "_id": "user_first",
            "email": "dupe@example.com",
            "password_hash": "x",
            "role": "user",
            "created_at": 1,
            "updated_at": 1,
        }
    )
    with pytest.raises(DuplicateKeyError):
        await coll.insert_one(
            {
                "_id": "user_second",
                "email": "dupe@example.com",
                "password_hash": "y",
                "role": "user",
                "created_at": 2,
                "updated_at": 2,
            }
        )
