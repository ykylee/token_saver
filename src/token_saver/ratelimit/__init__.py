"""Rate limit: Redis sliding window.

Responsibility (architecture.md §4.1):
- ``sliding_window.py`` — per-user, per-minute counter via Redis INCR + EX.
- 429 ``Too Many Requests`` response when limit exceeded.

Limit default and burst policy live in ``Settings`` (env-overridable);
the algorithm itself is fixed to a 60s sliding window for v1.
"""

from __future__ import annotations
