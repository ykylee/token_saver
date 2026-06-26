"""Admin bootstrap helpers — currently ``seed_admin`` only.

Why this lives in its own module:

- ``seed_admin`` is **idempotent** — re-running with the same email
  does NOT reset the password. That's deliberate: an operator who
  rotated the password locally should not be silently overwritten
  by a re-deploy. ``upsert`` already does the right thing for new
  users; this module wraps it with the "skip-if-exists" policy that
  matches a real bootstrap.
- ``seed_admin`` is called both from the FastAPI lifespan (so
  docker-compose "just works") and from the future CLI
  (``token-saver admin seed-admin``). Centralising here keeps both
  call sites identical.

Password rotation policy belongs in the future admin CLI; this
module stays minimal so the contract it offers is exactly one
function with one behaviour.
"""

from __future__ import annotations

from token_saver.auth.repository import UserStore
from token_saver.config import Settings
from token_saver.models import UserRecord

__all__ = ["seed_admin", "SeedAdminSkipped"]


class SeedAdminSkipped(Exception):
    """Raised when ``seed_admin`` was a no-op because the admin already exists.

    Not an error — operators use the exit code to distinguish "first
    boot" from "subsequent boot" when scripting around the CLI.
    """


async def seed_admin(settings: Settings, user_store: UserStore) -> UserRecord:
    """Insert the configured admin user if missing.

    - Returns the existing ``UserRecord`` if ``settings.admin_email``
      already exists (and raises :class:`SeedAdminSkipped` so the
      caller can distinguish "created" from "skipped").
    - Returns the freshly-created ``UserRecord`` if the user was new.
    - No-op (returns a synthetic empty record + raises) if neither
      ``settings.admin_email`` nor ``settings.admin_password`` is set.

    Idempotency is **strict**: this never overwrites an existing
    password. Operators wanting rotation must do it explicitly via the
    admin CLI (TASK-002-7) — silent rotation would mask a real
    security event.
    """
    if not settings.admin_email or not settings.admin_password:
        # Operator chose not to seed an admin (e.g. a CI runner with
        # no login surface). Bail out cleanly.
        raise SeedAdminSkipped("no admin_email/admin_password configured")

    existing = await user_store.get_by_email(settings.admin_email)
    if existing is not None:
        raise SeedAdminSkipped(f"admin {settings.admin_email!r} already exists")

    return await user_store.upsert(
        email=settings.admin_email,
        password=settings.admin_password,
        role="admin",
    )
