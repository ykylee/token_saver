"""Compressor registry.

Responsibility (architecture.md §3 step 6):
- ``registry.py`` — pluggable ``Compressor`` Protocol + ``@register`` decorator.
- Default impls: JSON compressor, text-trim compressor.
- CCR-lite hooks for reversibility (compress → store hash → on hit, decompress).

P1 may add an LLM-summarisation compressor; the registry is the seam.
"""

from __future__ import annotations
