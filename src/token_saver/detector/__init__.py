"""Content type detector.

Responsibility (architecture.md §3 step 4):
- ``classifier.py`` — heuristic over the joined message contents to decide
  ``mode ∈ {text, json, code, log}`` which in turn drives the compressor
  registry's lookup.

The detector is intentionally lossless — it never mutates the request.
P1 will add an optional LLM-based disambiguator (token-router style).
"""

from __future__ import annotations
