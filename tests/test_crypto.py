"""Tests for :mod:`token_saver.auth.crypto`.

The encryption helpers underpin the ``providers`` collection
(architecture §4.4). We cover:

- Round-trip: encrypt → decrypt yields the original.
- Nonce uniqueness: two encryptions of the same plaintext produce
  different ciphertexts (the 12-byte random nonce does its job).
- Tampering: flipping a byte makes the tag check fail.
- Master key validation: bad base64 / wrong length fail loudly.
"""

from __future__ import annotations

import base64

import pytest

from token_saver.auth.crypto import (
    InvalidCiphertextError,
    InvalidMasterKeyError,
    decrypt_secret,
    derive_master_key,
    encrypt_secret,
)

# A valid 32-byte master key (base64-encoded). Decodes to
# 32 zero bytes — fine for tests; we only care about the key
# length being correct.
_MASTER_B64 = base64.b64encode(b"\x00" * 32).decode("ascii")
_MASTER = derive_master_key(_MASTER_B64)


def test_roundtrip_returns_original() -> None:
    """Encrypt then decrypt yields the original plaintext."""
    plain = "sk-fake-very-secret-token-12345"
    token = encrypt_secret(plain, master_key=_MASTER)
    assert decrypt_secret(token, master_key=_MASTER) == plain


def test_nonce_uniqueness_per_call() -> None:
    """Two encryptions of the same plaintext produce different ciphertexts."""
    plain = "sk-fake-token"
    a = encrypt_secret(plain, master_key=_MASTER)
    b = encrypt_secret(plain, master_key=_MASTER)
    assert a != b, "AES-GCM should randomise the nonce on every call"


def test_tampered_ciphertext_fails_tag_check() -> None:
    """Flipping a byte in the ciphertext breaks the GCM tag → ``InvalidCiphertextError``."""
    plain = "sk-fake-token"
    token = encrypt_secret(plain, master_key=_MASTER)
    raw = bytearray(base64.b64decode(token))
    # Flip the last byte (inside the tag region).
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(InvalidCiphertextError):
        decrypt_secret(tampered, master_key=_MASTER)


def test_truncated_ciphertext_fails() -> None:
    """A blob shorter than the nonce prefix can't be decrypted."""
    raw = b"short"
    short_token = base64.b64encode(raw).decode("ascii")
    with pytest.raises(InvalidCiphertextError):
        decrypt_secret(short_token, master_key=_MASTER)


def test_non_base64_ciphertext_fails() -> None:
    """Garbage in → ``InvalidCiphertextError``, not a 500."""
    with pytest.raises(InvalidCiphertextError):
        decrypt_secret("not-valid-base64!!!", master_key=_MASTER)


def test_master_key_must_be_32_bytes() -> None:
    """Short / long base64 master keys fail validation at startup."""
    with pytest.raises(InvalidMasterKeyError):
        derive_master_key(base64.b64encode(b"\x00" * 16).decode("ascii"))
    with pytest.raises(InvalidMasterKeyError):
        derive_master_key(base64.b64encode(b"\x00" * 64).decode("ascii"))


def test_master_key_must_be_valid_base64() -> None:
    """Non-base64 input fails rather than silently truncating."""
    with pytest.raises(InvalidMasterKeyError):
        derive_master_key("not-valid-base64!!!")


def test_encrypt_rejects_wrong_key_length() -> None:
    """Defensive: ``encrypt_secret`` refuses a non-32-byte master key."""
    with pytest.raises(InvalidMasterKeyError):
        encrypt_secret("hello", master_key=b"short")
