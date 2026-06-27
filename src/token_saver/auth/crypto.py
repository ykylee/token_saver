"""Crypto helpers — password hashing + token generation + AES-GCM secret encryption.

Three responsibilities, all in one place so the algorithm choice
(``argon2-cffi`` for passwords, ``cryptography`` AES-GCM for secrets)
lives behind one module boundary:

1. **Password hashing** (``hash_password`` / ``verify_password``) —
   argon2id. Used by the user store and the ``/v1/auth/login`` flow.
2. **Token generation** (``generate_token``) — 256-bit URL-safe bearer
   tokens. Used by ``/v1/auth/login``.
3. **Secret encryption** (``encrypt_secret`` / ``decrypt_secret``) —
   AES-256-GCM with a 12-byte random nonce per call. Used to
   envelope ``api_key`` and ``base_url`` on the way into Mongo
   (``providers`` collection, architecture §4.4). Master key lives
   in ``Settings.master_key`` (``TOKEN_SAVER_MASTER_KEY`` env var);
   loss of the master key = permanent loss of stored credentials,
   which is the intended failure mode.

Security note: ``hash_password`` is intentionally NOT exposed as an
endpoint helper. New-user creation flows (``auth.seed.seed_admin``,
the future admin CLI) own the hashing call site so we never confuse
plain text with the hash on the wire. The same principle applies to
``encrypt_secret`` — the store does the encryption on insert and
decryption on read, callers never see the raw cipher blob.
"""

from __future__ import annotations

import base64
import binascii
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "generate_token",
    "hash_password",
    "verify_password",
    "InvalidCredentialsError",
    "encrypt_secret",
    "decrypt_secret",
    "derive_master_key",
    "InvalidMasterKeyError",
    "InvalidCiphertextError",
]


# Module-level singleton — ``argon2-cffi`` reads are CPU-bound and the
# ``PasswordHasher`` is thread-safe. Re-using avoids per-call setup
# overhead when verifying many logins.
_HASHER = PasswordHasher()


# AES-GCM parameters. The nonce length is fixed by the spec (96 bits);
# changing it requires a different cipher mode. Tag length is also
# fixed (128 bits) by the GCM spec.
_NONCE_BYTES = 12
_KEY_BYTES = 32  # AES-256


class InvalidCredentialsError(Exception):
    """Raised when password verification fails.

    Caught by the auth route and translated into a 401 with a generic
    "invalid credentials" message — we never leak which side of the
    pair (unknown email vs. wrong password) failed.
    """


class InvalidMasterKeyError(ValueError):
    """Raised when ``Settings.master_key`` is not a valid AES-256 key.

    Operators see this at startup, before the proxy serves its first
    request — better than decrypting into garbage and 502-ing every
    upstream call.
    """


class InvalidCiphertextError(ValueError):
    """Raised when a stored ciphertext fails the AES-GCM tag check.

    Either the master key changed (lost key scenario) or the row was
    tampered with. Operators see this when reloading a backup with a
    different key — fail loudly, not silently with the wrong cleartext.
    """


def generate_token() -> str:
    """Return a 256-bit URL-safe bearer token.

    ``secrets.token_urlsafe(32)`` gives ~43 chars of base64 entropy,
    matching the architecture spec §4.1 (``session:{token}`` key shape).
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


def derive_master_key(b64_key: str) -> bytes:
    """Decode a base64-encoded master key and validate the AES-256 length.

    The ``Settings.master_key`` field is stored base64-encoded so
    operators can copy/paste a 32-byte value without escaping headaches.
    We refuse anything that doesn't decode to exactly 32 bytes
    (AES-256) so a typo never silently degrades to AES-128 or worse.
    """
    try:
        raw = base64.b64decode(b64_key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidMasterKeyError(
            f"master_key is not valid base64: {exc}"
        ) from exc
    if len(raw) != _KEY_BYTES:
        raise InvalidMasterKeyError(
            f"master_key must decode to {_KEY_BYTES} bytes (AES-256); "
            f"got {len(raw)} bytes"
        )
    return raw


def encrypt_secret(plain: str, *, master_key: bytes) -> str:
    """Encrypt ``plain`` with AES-GCM and return base64(nonce || ciphertext).

    The nonce is fresh on every call (12 random bytes from
    ``secrets``) and prepended to the ciphertext so we can decrypt
    without storing the nonce separately. AES-GCM authenticates the
    ciphertext as a side effect — tampered blobs fail tag verification
    in :func:`decrypt_secret` rather than returning garbage.

    The output is base64 so it survives a JSON wire format and BSON
    ``string`` storage (Mongo's ``Binary`` type would also work, but
    string keeps the docs readable for ad-hoc inspection).
    """
    if len(master_key) != _KEY_BYTES:
        raise InvalidMasterKeyError(
            f"master_key must be {_KEY_BYTES} bytes; got {len(master_key)}"
        )
    cipher = AESGCM(master_key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = cipher.encrypt(nonce, plain.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(token: str, *, master_key: bytes) -> str:
    """Reverse :func:`encrypt_secret`.

    Raises :class:`InvalidCiphertextError` on any failure (bad base64,
    short token, tag mismatch). The route layer logs this as
    "decryption failed" without leaking which row tripped it.
    """
    if len(master_key) != _KEY_BYTES:
        raise InvalidMasterKeyError(
            f"master_key must be {_KEY_BYTES} bytes; got {len(master_key)}"
        )
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise InvalidCiphertextError(f"ciphertext is not valid base64: {exc}") from exc
    if len(raw) <= _NONCE_BYTES:
        raise InvalidCiphertextError("ciphertext is shorter than the nonce prefix")
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    cipher = AESGCM(master_key)
    try:
        plain_bytes = cipher.decrypt(nonce, ciphertext, associated_data=None)
    except Exception as exc:  # cryptography raises a generic Exception on tag mismatch
        raise InvalidCiphertextError(f"decryption failed: {exc}") from exc
    return plain_bytes.decode("utf-8")
