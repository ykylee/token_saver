"""Behavioural tests for :class:`InMemoryProviderStore`.

The in-memory impl is the test-friendly backend that mirrors
:class:`MongoProviderStore`'s isolation contract. We cover the full
CRUD surface plus the per-tenant scoping rules:

- ``list_for_user(None)`` returns all rows (admin view).
- ``list_for_user(<id>)`` filters to that owner's rows.
- ``get(id, None)`` matches any owner.
- ``get(id, <id>)`` filters by owner.
- ``delete(id, <id>)`` only removes if the owner matches.

Encryption isn't exercised here — the in-memory store doesn't
encrypt (it's the in-process working copy); Mongo tests cover the
encryption round-trip.
"""

from __future__ import annotations

import pytest

from token_saver.models import ProviderRecord
from token_saver.provider.store import (
    InMemoryProviderStore,
    ProviderStore,
    _MemoryRow,
    _row_to_record,
)


async def test_create_returns_record_with_id_and_owner() -> None:
    """A new provider gets a ULID-shaped id and the supplied owner."""
    store: ProviderStore = InMemoryProviderStore()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-fake-key",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    assert record.id.startswith("provider_")
    assert record.owner_user_id == "user_alice"
    assert record.name == "openai-main"
    assert record.type == "openai"
    assert record.base_url == "https://api.openai.com"
    assert record.api_key == "sk-fake-key"
    assert record.default_model == "gpt-4o-mini"
    assert record.enabled is True


async def test_get_returns_existing_record() -> None:
    store: ProviderStore = InMemoryProviderStore()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="sk-fake-key",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    found = await store.get(record.id, None)
    assert found is not None
    assert found.id == record.id


async def test_get_returns_none_for_missing_id() -> None:
    store: ProviderStore = InMemoryProviderStore()
    assert await store.get("provider_missing", None) is None


async def test_list_for_user_filters_by_owner() -> None:
    store: ProviderStore = InMemoryProviderStore()
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
    assert len(alice_rows) == 1
    assert alice_rows[0].owner_user_id == "user_alice"

    bob_rows = await store.list_for_user("user_bob")
    assert len(bob_rows) == 1
    assert bob_rows[0].owner_user_id == "user_bob"


async def test_list_for_user_none_returns_all() -> None:
    """``user_id=None`` is the admin's cross-tenant view."""
    store: ProviderStore = InMemoryProviderStore()
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
    all_rows = await store.list_for_user(None)
    owners = {r.owner_user_id for r in all_rows}
    assert owners == {"user_alice", "user_bob"}


async def test_get_filters_by_owner_when_user_id_supplied() -> None:
    """Regular users can't fetch another user's provider."""
    store: ProviderStore = InMemoryProviderStore()
    bob_record = await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    # Admin (None) sees it.
    assert await store.get(bob_record.id, None) is not None
    # Alice (wrong owner) doesn't.
    assert await store.get(bob_record.id, "user_alice") is None
    # Bob (correct owner) does.
    assert await store.get(bob_record.id, "user_bob") is not None


async def test_update_modifies_supplied_fields() -> None:
    store: ProviderStore = InMemoryProviderStore()
    record = await store.create(
        owner_user_id="user_alice",
        name="openai-main",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
    )
    updated = await store.update(
        record.id,
        owner_user_id="user_alice",
        name="openai-renamed",
        enabled=False,
    )
    assert updated is not None
    assert updated.name == "openai-renamed"
    assert updated.enabled is False
    # Untouched fields preserved.
    assert updated.base_url == "https://api.openai.com"
    assert updated.default_model == "gpt-4o-mini"
    assert updated.id == record.id


async def test_update_returns_none_for_missing_id() -> None:
    store: ProviderStore = InMemoryProviderStore()
    assert (
        await store.update(
            "provider_missing",
            owner_user_id="user_alice",
            name="x",
        )
        is None
    )


async def test_update_enforces_owner_for_non_admin() -> None:
    """Regular users can't update another user's provider."""
    store: ProviderStore = InMemoryProviderStore()
    bob_record = await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    # Admin (None) can update.
    assert (
        await store.update(
            bob_record.id,
            owner_user_id=None,
            name="admin-renamed",
        )
        is not None
    )
    # Alice (wrong owner) cannot.
    assert (
        await store.update(
            bob_record.id,
            owner_user_id="user_alice",
            name="alice-renamed",
        )
        is None
    )


async def test_delete_removes_row() -> None:
    store: ProviderStore = InMemoryProviderStore()
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
    store: ProviderStore = InMemoryProviderStore()
    assert await store.delete("provider_missing", None) is False


async def test_delete_enforces_owner_for_non_admin() -> None:
    store: ProviderStore = InMemoryProviderStore()
    bob_record = await store.create(
        owner_user_id="user_bob",
        name="ollama-gpu",
        type="ollama",
        base_url="http://gpu:11434",
        api_key=None,
        default_model="llama3.1:8b",
        enabled=True,
    )
    # Alice can't delete Bob's.
    assert await store.delete(bob_record.id, "user_alice") is False
    # Admin can.
    assert await store.delete(bob_record.id, None) is True


async def test_set_models_cache_round_trip() -> None:
    """Refresh writes the catalog back; subsequent get reads it."""
    store: ProviderStore = InMemoryProviderStore()
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
    # The record surfaces the same fields (the in-memory impl doesn't
    # expose the cached list directly; that lives in the Mongo sub-doc).
    assert refreshed.id == record.id


async def test_list_enabled_for_user_filters_enabled() -> None:
    """Disabled rows are excluded from the chat-router lookup."""
    store: ProviderStore = InMemoryProviderStore()
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
        owner_user_id="user_alice",
        name="openai-disabled",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4",
        enabled=False,
    )
    enabled = await store.list_enabled_for_user("user_alice")
    assert len(enabled) == 1
    assert enabled[0].name == "openai-main"


async def test_row_to_record_projects_fields() -> None:
    """Internal ``_MemoryRow`` → ``ProviderRecord`` preserves the surface."""
    row = _MemoryRow(
        id="provider_x",
        name="n",
        type="openai",
        base_url="https://api.openai.com",
        api_key="k",
        default_model="gpt-4o-mini",
        enabled=True,
        owner_user_id="user_alice",
        created_at=1700000000,
    )
    record: ProviderRecord = _row_to_record(row)
    assert record.id == "provider_x"
    assert record.type == "openai"
    assert record.api_key == "k"


@pytest.mark.parametrize("user_id", [None, "user_alice"])
async def test_list_for_user_empty_store(user_id: str | None) -> None:
    """Empty store → empty list, never ``None``."""
    store: ProviderStore = InMemoryProviderStore()
    rows = await store.list_for_user(user_id)
    assert rows == []
