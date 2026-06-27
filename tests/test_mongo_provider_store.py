"""Behavioural tests for :class:`MongoProviderStore`.

We exercise the Mongo-backed impl with ``mongomock-motor`` (no real
Mongo needed). Coverage:

- Round-trip the encrypted ``api_key`` / ``base_url`` (insert
  decrypts back to the original on read).
- Multi-tenant isolation: ``list_for_user`` and ``get`` honour the
  ``user_id`` filter.
- Index creation is idempotent.
"""

from __future__ import annotations

import base64

import pytest
from mongomock_motor import AsyncMongoMockClient

from token_saver.auth.crypto import InvalidCiphertextError, derive_master_key
from token_saver.provider.store import (
    PROVIDERS_COLLECTION,
    MongoProviderStore,
)

_MASTER_B64 = base64.b64encode(b"\x42" * 32).decode("ascii")
_MASTER = derive_master_key(_MASTER_B64)


def _new_store() -> tuple[MongoProviderStore, AsyncMongoMockClient]:
    client = AsyncMongoMockClient()
    store = MongoProviderStore(
        client=client, db_name="token_saver_test", master_key=_MASTER
    )
    return store, client


async def test_create_then_get_round_trips_plaintext() -> None:
    """Stored ciphertext decrypts back to the supplied plaintext on read."""
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-fake-very-secret-token",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    found = await store.get(record.id, None)
    assert found is not None
    assert found.api_key == "sk-fake-very-secret-token"
    assert found.base_url == "https://api.openai.com"


async def test_create_persists_ciphertext_not_plaintext() -> None:
    """Defence-in-depth: a direct Mongo read shows cipher, not plain text."""
    store, client = _new_store()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-must-not-appear",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    raw = await client["token_saver_test"][PROVIDERS_COLLECTION].find_one(
        {"_id": record.id}
    )
    assert raw is not None
    assert raw["api_key_enc"] != "sk-must-not-appear"
    assert "sk-must-not-appear" not in str(raw["base_url_enc"])


async def test_list_for_user_filters_by_owner() -> None:
    store, _ = _new_store()
    await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    alice_rows = await store.list_for_user("user_alice")
    assert {r.owner_user_id for r in alice_rows} == {"user_alice"}

    all_rows = await store.list_for_user(None)
    assert {r.owner_user_id for r in all_rows} == {"user_alice", "user_bob"}


async def test_get_filters_by_owner() -> None:
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    assert await store.get(record.id, "user_alice") is None
    assert await store.get(record.id, "user_bob") is not None
    assert await store.get(record.id, None) is not None


async def test_update_replaces_ciphertext_with_fresh() -> None:
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-old",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    updated = await store.update(
        record.id,
        owner_user_id="user_alice",
        api_key="sk-rotated",
    )
    assert updated is not None
    assert updated.api_key == "sk-rotated"


async def test_delete_removes_row() -> None:
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    assert await store.delete(record.id, None) is True
    assert await store.get(record.id, None) is None


async def test_delete_returns_false_for_missing() -> None:
    store, _ = _new_store()
    assert await store.delete("provider_missing", None) is False


async def test_delete_enforces_owner() -> None:
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    assert await store.delete(record.id, "user_alice") is False
    assert await store.delete(record.id, None) is True


async def test_set_models_cache_persists_and_returns_record() -> None:
    store, _ = _new_store()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    models: list[dict[str, object]] = [
        {"id": "gpt-4o-mini", "owned_by": "openai"},
        {"id": "gpt-4o", "owned_by": "openai"},
    ]
    refreshed = await store.set_models_cache(record.id, None, models=models)
    assert refreshed is not None
    assert refreshed.id == record.id


async def test_list_enabled_for_user_filters_disabled() -> None:
    store, _ = _new_store()
    await store.create(
        owner_user_id="user_alice",
        name="openai-on",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    await store.create(
        owner_user_id="user_alice",
        name="openai-off",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4",
        enabled=False,
    )
    enabled = await store.list_enabled_for_user("user_alice")
    assert [r.name for r in enabled] == ["openai-on"]


async def test_ensure_indexes_is_idempotent() -> None:
    """Calling ``ensure_indexes`` twice doesn't raise."""
    store, client = _new_store()
    await store.ensure_indexes()
    await store.ensure_indexes()
    info = await client["token_saver_test"][PROVIDERS_COLLECTION].index_information()
    # The unique email-style indexes land as well as the simple
    # single-key ones. We just confirm the keys we expect are there.
    assert "owner_user_id_1" in info
    assert "type_1" in info


async def test_constructor_rejects_wrong_key_length() -> None:
    """Defence-in-depth: a short master key is rejected at construction."""
    client = AsyncMongoMockClient()
    with pytest.raises(ValueError, match="32 bytes"):
        MongoProviderStore(
            client=client,
            db_name="token_saver_test",
            master_key=b"too-short",
        )


async def test_store_with_wrong_key_fails_to_decrypt() -> None:
    """Decryption with the wrong master key raises."""
    store_a, _ = _new_store()
    record = await store_a.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    # Force a read with a different valid key — same client, but
    # the cipher was wrapped with the original master key, so
    # the tag check fails and we surface as ``InvalidCiphertextError``.
    bad_key = derive_master_key(base64.b64encode(b"\x99" * 32).decode("ascii"))
    store_a._master_key = bad_key  # type: ignore[attr-defined]
    with pytest.raises(InvalidCiphertextError):
        await store_a.get(record.id, None)
