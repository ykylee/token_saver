"""HTTP routers grouped by surface.

Each module mounts an ``APIRouter`` that ``proxy.app.create_app``
includes at startup. Splitting by route family keeps the boot wiring
short and lets route-specific helpers (auth dep, RBAC dep) stay
adjacent to their handlers.

Sub-tasks own one router at a time:
- ``auth``             — TASK-002-3 (login + bearer issue).
- ``chat_completions`` — TASK-002-2 (mock) → TASK-002-5 (real forward).
- ``models``           — TASK-002-2 (stub) → TASK-002-5 (Provider Registry).
- ``admin``            — TASK-002-2 (health stub) → TASK-002-3+ (RBAC).
"""

from __future__ import annotations

from token_saver.proxy.routes import admin, auth, chat_completions, models

__all__ = ["admin", "auth", "chat_completions", "models"]
