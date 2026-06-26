"""CCR-lite: compression-cache-read.

Responsibility (architecture.md §3 step 5 / step 9):
- ``store.py`` — Redis read-through + Mongo cold store.
- ``hash.py`` — content hash for cache key (``sha256`` of normalised messages).

The split mirrors headroom's CCR design but uses Redis for hot reads
(multi-user rate-limit-friendly) and Mongo for durable storage with a
TTL index. ``hits`` counter lives in Mongo and increments async.
"""

from __future__ import annotations
