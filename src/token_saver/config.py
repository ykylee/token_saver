"""Runtime configuration for token_saver.

Centralized via Pydantic Settings. Reads from:

1. **Environment variables** (highest priority) — overrides everything.
2. **``.env``** file at the project root (or wherever ``Settings.model_config``
   points via ``env_file``) — local development defaults.

Per-user secrets (provider API keys, base URLs) are NOT loaded here —
they live encrypted in MongoDB and are pulled per-request by the
Provider Registry.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Process-wide settings.

    Field naming convention: ``TOKEN_SAVER_<SECTION>_<KEY>`` maps to
    ``<section>_<key>`` here. Example: ``TOKEN_SAVER_REDIS_URL`` →
    ``Settings.redis_url``.

    Settings that are deployment-time but **not** secrets (URLs, ports,
    log levels, rate-limit knobs) live here. Anything user-specific
    lives in MongoDB.
    """

    model_config = SettingsConfigDict(
        env_prefix="TOKEN_SAVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- Server -----
    host: str = Field(default="0.0.0.0", description="FastAPI bind host.")
    port: int = Field(default=8787, ge=1, le=65535)
    workers: int = Field(default=1, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ----- Storage -----
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (hot storage).",
    )
    mongo_url: str = Field(
        default="mongodb://localhost:27017/token_saver",
        description="MongoDB connection URL (cold storage).",
    )
    mongo_db_name: str = Field(default="token_saver")
    # User store backend. ``memory`` keeps tests + local dev hermetic;
    # ``mongo`` is what docker-compose and production run. The factory
    # in ``auth.factory`` picks the impl at startup.
    user_store_backend: Literal["memory", "mongo"] = "memory"
    # Provider store backend (TASK-002-5-b). Same memory/mongo split
    # as the user store; lives in its own factory
    # (``provider.factory``) so the two can be swapped independently.
    provider_store_backend: Literal["memory", "mongo"] = "memory"

    # ----- Auth / crypto -----
    master_key: str = Field(
        # 32-byte url-safe base64 placeholder; production REQUIRES override.
        default="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        description=(
            "AES-GCM master key for provider credential encryption. "
            "MUST be overridden via env (TOKEN_SAVER_MASTER_KEY) in production."
        ),
    )
    session_ttl_seconds: int = Field(default=3600, ge=60)
    rate_limit_per_minute: int = Field(default=60, ge=1)

    # ----- CCR-lite -----
    ccr_redis_ttl_seconds: int = Field(default=300, ge=1)
    ccr_mongo_ttl_days: int = Field(default=30, ge=1)

    # ----- Provider registry -----
    provider_models_cache_ttl_seconds: int = Field(default=3600, ge=60)
    provider_health_ttl_seconds: int = Field(default=30, ge=1)
    provider_request_timeout_seconds: float = Field(default=60.0, gt=0)

    # ----- Admin seed (one-time bootstrap) -----
    admin_email: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-singleton ``Settings`` instance.

    Cached so repeated calls (which happen on every request via
    dependency injection) don't re-parse the environment.
    """
    return Settings()
