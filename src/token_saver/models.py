"""Pydantic schemas — OpenAI-compatible request/response shapes.

These are the on-the-wire types the proxy speaks. Two rules:

1. **Field names match OpenAI exactly.** Clients (Claude Code, Codex,
   OpenCode, the OpenAI Python SDK, litellm, …) read these by name.
   Renaming ``messages`` to ``conversation`` would silently break every
   client in the wild.

2. **Extra fields are tolerated, not rejected.** Clients send vendor
   extensions (``metadata``, ``safety_identifier``, …) we don't model.
   ``extra="allow"`` keeps us forward-compatible without leaking
   ``model_config`` knobs into every endpoint.

Streaming variants and tool/function calling land in later cycles
(TASK-002-5/6). What's here is enough to lock in the wire shape so
the Provider Registry work has a stable contract to call into.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatCompletionRequest",
    "ChatMessage",
    "ChatCompletionResponse",
    "ChatCompletionChoice",
    "Usage",
    "ErrorEnvelope",
    "ErrorBody",
    "ModelCard",
    "ModelList",
    "HealthResponse",
    "LoginRequest",
    "LoginResponse",
    "CurrentUser",
    "UserRecord",
    "Role",
    "FinishReason",
    "UserRole",
    "ProviderType",
    "ProviderTestRequest",
    "ProviderTestResult",
    "ProviderCreateRequest",
    "ProviderRecord",
    "ProviderInfo",
    "ModelsRefreshResult",
]

Role = Literal["system", "user", "assistant", "tool", "developer", "function"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "function_call"]
UserRole = Literal["admin", "user"]
ProviderType = Literal["openai", "anthropic", "ollama", "vllm"]


class _OpenAIBase(BaseModel):
    """Shared config: tolerate unknown fields, no ``None`` ↔ missing dance."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChatMessage(_OpenAIBase):
    """A single message in a chat conversation.

    ``content`` accepts either a plain string (most common) or a list
    of content parts (vision, mixed text/image). We keep the union loose
    here — Provider clients in ``token_saver.provider.*`` validate the
    parts against the upstream contract.
    """

    role: Role
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = Field(default=None, alias="tool_call_id")


class ChatCompletionRequest(_OpenAIBase):
    """POST body for ``/v1/chat/completions``.

    ``model`` and ``messages`` are required; everything else is OpenAI's
    sampling/penalty/limit surface. We default ``n=1`` and ``stream=false``
    to match OpenAI's behaviour. ``stream=True`` is rejected at the route
    level until TASK-002-6 wires up SSE passthrough.
    """

    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    n: int | None = Field(default=None, ge=1, le=8)
    max_tokens: int | None = Field(default=None, ge=1)
    stream: bool = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    logit_bias: dict[str, float] | None = None
    user: str | None = None
    # Provider-prefix routing (token-saver extension; not in OpenAI core).
    # ``openai/gpt-4o-mini`` → provider=openai, model=gpt-4o-mini.
    # TASK-002-5 owns the parse; the field exists now so the wire shape
    # is stable across the sub-task cycle.
    provider: str | None = None


class ChatCompletionChoice(_OpenAIBase):
    """One row of ``choices`` in the response."""

    index: int
    message: ChatMessage
    finish_reason: FinishReason = "stop"


class Usage(_OpenAIBase):
    """Token accounting block — OpenAI-compatible.

    Numbers are **estimates** until TASK-002-5 wires real upstream
    usage. The mock heuristic lives in ``routes.chat_completions``.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(_OpenAIBase):
    """Response body for ``/v1/chat/completions``.

    Matches OpenAI ChatCompletion schema so SDKs and clients treat us
    as a drop-in provider.
    """

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage
    # token-saver extension: surfaces the routing decision so users can
    # see which provider served them. Populated from TASK-002-5 onwards.
    routed_provider: str | None = None


# ----- Errors -----


class ErrorBody(_OpenAIBase):
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class ErrorEnvelope(_OpenAIBase):
    error: ErrorBody


# ----- /v1/models -----


class ModelCard(_OpenAIBase):
    """One entry in the ``/v1/models`` response ``data`` array.

    ``owned_by`` mirrors OpenAI's convention (``openai``, ``anthropic``,
    ``ollama``, ``vllm``, …) — Provider Registry reuses this string to
    bucket models by vendor.
    """

    id: str
    object: Literal["model"] = "model"
    owned_by: str
    created: int | None = None


class ModelList(_OpenAIBase):
    object: Literal["list"] = "list"
    data: list[ModelCard]


# ----- /admin/health -----


class HealthResponse(_OpenAIBase):
    status: Literal["ok", "degraded", "down"] = "ok"
    version: str
    schema_version: int
    uptime_seconds: float


# ----- Auth -----


class LoginRequest(_OpenAIBase):
    """POST body for ``POST /v1/auth/login``."""

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class LoginResponse(_OpenAIBase):
    """Successful login response. ``token`` is the bearer credential."""

    token: str
    expires_in: int = Field(ge=1)
    role: UserRole
    user_id: str


class CurrentUser(_OpenAIBase):
    """Identity attached to a request after the auth dependency runs.

    Pulled out of the Redis session payload and propagated via
    ``request.state.user`` / FastAPI dependency injection. Routes that
    need an authenticated identity declare ``Depends(get_current_user)``
    (or one of the stricter role gates).
    """

    id: str
    role: UserRole
    email: str | None = None


class UserRecord(_OpenAIBase):
    """A row in the user store.

    ``password_hash`` is an argon2id hash (NEVER plain text). The store
    is responsible for hashing on insert; verification happens via
    ``auth.crypto.verify_password``.
    """

    id: str
    email: str
    password_hash: str
    role: UserRole
    created_at: int


# ----- Providers -----


class ProviderTestRequest(_OpenAIBase):
    """POST body for ``POST /v1/providers/test`` (TASK-002-5-b).

    Operator-supplied credentials + URL are run through the
    provider's ``test_connection`` and the verdict returned in a
    :class:`ProviderTestResult`. No persistence happens here — the
    operator reviews the result before they hit ``POST /v1/providers``.
    """

    type: ProviderType
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    default_model: str | None = Field(default=None, max_length=256)


class ProviderTestResult(_OpenAIBase):
    """Outcome of a connection test.

    Mirrors :class:`provider.base.ProviderTestResult` so the wire
    shape and the in-process shape stay in lock-step.
    """

    ok: bool
    latency_ms: int
    models_count: int = 0
    sample_models: list[ModelCard] = []
    error: str | None = None


class ProviderCreateRequest(_OpenAIBase):
    """POST body for ``POST /v1/providers`` (TASK-002-5-b).

    On success: the provider is persisted (Mongo providers
    collection, encrypted at rest) and registered with the
    in-process :class:`ProviderRegistry`.
    """

    name: str = Field(min_length=1, max_length=128)
    type: ProviderType
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    default_model: str = Field(min_length=1, max_length=256)
    enabled: bool = True


class ProviderInfo(_OpenAIBase):
    """Operator-facing summary of a registered provider.

    API key is *never* included — we expose the type + URL + model
    choice so the operator can identify the row, but secrets stay
    server-side.
    """

    id: str
    name: str
    type: ProviderType
    base_url: str
    default_model: str
    enabled: bool
    owner_user_id: str | None = None


class ProviderRecord(_OpenAIBase):
    """Internal record stored in Mongo / in-memory (TASK-002-5-b).

    Differs from :class:`ProviderInfo` in two ways:

    - Holds the **decrypted** ``api_key`` (already past the AES-GCM
      layer). Never returned over the wire.
    - Tracks ``owner_user_id`` for tenant isolation (an admin
      ``GET /v1/providers`` lists across users; a regular user
      only sees their own).
    """

    id: str
    name: str
    type: ProviderType
    base_url: str
    api_key: str | None
    default_model: str
    enabled: bool
    owner_user_id: str


class ModelsRefreshResult(_OpenAIBase):
    """Response for ``POST /v1/providers/{id}/models/refresh``.

    ``added`` and ``removed`` are deltas versus the previously
    cached catalog so an operator can see exactly what the refresh
    changed.
    """

    provider_id: str
    models_count: int
    added: list[str] = []
    removed: list[str] = []
