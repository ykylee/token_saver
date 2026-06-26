"""Crypto helpers — password hashing + token generation.

Wraps ``argon2-cffi`` (argon2id) for password verification and
``secrets`` for bearer-token entropy. The interface is small on
purpose: callers should never reach for the underlying library
directly. If we ever swap algorithms (e.g. argon2id → argon2id w/
different params), this module is the only file that needs to change.

Security note: ``hash_password`` is intentionally NOT exported as an
endpoint helper. New-user creation flows (TASK-002-4 Mongo bootstrap)
own the hashing call site so we never confuse plain text with the
hash on the wire.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

__all__ = ["generate_token", "hash_password", "verify_password", "InvalidCredentialsError"]


# Module-level singleton — ``argon2-cffi`` reads are CPU-bound and the
# ``PasswordHasher`` is thread-safe. Re-using avoids per-call setup
# overhead when verifying many logins.
_HASHER = PasswordHasher()


class InvalidCredentialsError(Exception):
    """Raised when password verification fails.

    Caught by the auth route and translated into a 401 with a generic
    "invalid credentials" message — we never leak which side of the
    pair (unknown email vs. wrong password) failed.
    """


def generate_token() -> str:
    """Return a 256-bit URL-safe bearer token.

    ``secrets.token_urlsafe(32)`` gives ~43 chars of base64 entropy,
    matching the architecture spec §4.1 (`session:{token}` key shape).
    """
    return secrets.token_urlsafe(32)


def hash_password(plain: str) -> str:
    """Hash a plain-text password with argon2id.

    The returned string already encodes the salt and parameters; it's
    safe to persist verbatim. Do NOT pre-hash or strip whitespace.
    """
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> None:
    """Verify ``plain`` against the stored argon2id ``hashed`` value.

    Raises :class:`InvalidCredentialsError` on any failure
    (mismatch, malformed hash, expired parameters). Callers MUST NOT
    distinguish between "unknown email" and "wrong password" in their
    response — that asymmetry is what enables user enumeration.
    """
    try:
        _HASHER.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError) as exc:
        raise InvalidCredentialsError(str(exc)) from exc
