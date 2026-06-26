"""Behavioural tests for :func:`auth.seed.seed_admin`.

The seed function is the operator-facing bootstrap that decides
"who is the admin on first boot". It's strict-idempotent on
purpose — see the module docstring for the rotation policy.

``InMemoryUserStore.__init__`` already seeds an admin when
``settings.admin_email`` / ``settings.admin_password`` are set, so
these tests construct an *empty* store first (no admin configured),
then call ``seed_admin`` to exercise the function in isolation.
"""

from __future__ import annotations

import pytest

from token_saver.auth.repository import InMemoryUserStore
from token_saver.auth.seed import SeedAdminSkipped, seed_admin
from token_saver.config import Settings


def _settings(
    email: str | None = "admin@example.com",
    password: str | None = "changeme-please",
) -> Settings:
    return Settings(admin_email=email, admin_password=password)


def _empty_store() -> InMemoryUserStore:
    """An ``InMemoryUserStore`` with no auto-seeded admin.

    Passing ``admin_email=None`` / ``admin_password=None`` keeps the
    constructor's bootstrap logic dormant, so the test owns the
    admin lifecycle from here on.
    """
    return InMemoryUserStore(_settings(email=None, password=None))


async def test_seed_creates_admin_when_missing() -> None:
    """First boot creates the admin and returns the fresh record."""
    store = _empty_store()
    record = await seed_admin(_settings(), store)

    assert record.email == "admin@example.com"
    assert record.role == "admin"
    assert record.password_hash  # argon2id hash, not plain

    # Subsequent reads via the store find the record.
    found = await store.get_by_email("admin@example.com")
    assert found is not None
    assert found.id == record.id


async def test_seed_is_strict_idempotent_for_existing_admin() -> None:
    """Re-running seed does NOT overwrite the existing password."""
    store = _empty_store()

    first = await store.upsert(
        email="admin@example.com",
        password="operator-rotated-pw",
        role="admin",
    )

    with pytest.raises(SeedAdminSkipped):
        await seed_admin(_settings(), store)

    after = await store.get_by_email("admin@example.com")
    assert after is not None
    assert after.password_hash == first.password_hash  # untouched
    assert after.id == first.id


async def test_seed_skips_when_no_admin_configured() -> None:
    """No credentials → no-op (not an error)."""
    store = _empty_store()
    with pytest.raises(SeedAdminSkipped):
        await seed_admin(_settings(email=None, password=None), store)
    assert await store.get_by_email("admin@example.com") is None


async def test_seed_only_skips_when_email_present_but_password_missing() -> None:
    """Defensive: partial config still produces the skip signal."""
    store = _empty_store()
    with pytest.raises(SeedAdminSkipped):
        await seed_admin(_settings(email="x@example.com", password=None), store)


async def test_seed_normalises_email_case() -> None:
    """Capitalised email is stored lower-case; subsequent reads match."""
    store = _empty_store()
    record = await seed_admin(
        _settings(email="Admin@Example.COM", password="pw"), store
    )
    assert record.email == "admin@example.com"
    assert await store.get_by_email("ADMIN@example.com") is not None
