"""Auth: Bearer token middleware + RBAC + crypto helpers.

Responsibility (architecture.md §4.4 / §5):
- ``middleware.py`` — extract ``Authorization: Bearer ...`` → Redis session.
- ``rbac.py`` — admin/user role gate per RBAC matrix (§5.2).
- ``crypto.py`` — AES-GCM encrypt/decrypt for ``base_url`` and ``api_key``;
  argon2id hash for user passwords.

The master key for AES-GCM comes from ``Settings.master_key`` (env var
``TOKEN_SAVER_MASTER_KEY``). Loss of the master key = permanent loss of
provider credentials — by design.
"""

from __future__ import annotations
