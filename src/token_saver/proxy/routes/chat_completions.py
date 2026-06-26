"""POST /v1/chat/completions — OpenAI-compatible chat proxy.

TASK-002-2 ships a **mock response**. The shape, validation, and
accounting match OpenAI's contract so any OpenAI SDK can point at us
end-to-end; the *content* is generated locally with no upstream call.

What lands across the sub-task cycle:
- TASK-002-2 (this file): mock content + heuristic usage + 422/400 errors.
- TASK-002-3: Bearer token + RBAC dependency.
- TASK-002-5: provider routing, real upstream forwarding (httpx),
  Provider Registry model resolution.
- TASK-002-6: streaming (SSE passthrough), retry policy.

The mock surfaces a ``X-Token-Saver-Mock: true`` response header so
clients (and the fixture regression test) can confirm at a glance
that they're not talking to a real provider.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Response, status

from token_saver.models import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ErrorBody,
    ErrorEnvelope,
    Usage,
)

__all__ = ["router"]

router = APIRouter(tags=["chat"])


# Header we attach to mock responses. Clients/tests can sniff for this
# rather than parsing the body. Removed once TASK-002-5 wires real
# upstream forwarding.
MOCK_HEADER = "X-Token-Saver-Mock"
MOCK_HEADER_VALUE = "true"


def _estimate_tokens(messages: list[ChatMessage], completion: str) -> Usage:
    """Heuristic token usage — replaced by real upstream ``usage`` in TASK-002-5.

    Cheap and predictable: characters / 4 rounded up, the same rule of
    thumb OpenAI's tokenizer stats suggest for English prose. Good
    enough for tests + smoke; **never** the source of truth.
    """
    prompt_chars = sum(_content_chars(m.content) for m in messages)
    completion_chars = len(completion)
    prompt_tokens = max(1, (prompt_chars + 3) // 4)
    completion_tokens = max(1, (completion_chars + 3) // 4)
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _content_chars(content: str | list[dict[str, object]]) -> int:
    if isinstance(content, str):
        return len(content)
    # List-of-parts (vision / mixed). Conservative: sum JSON length.
    return sum(len(str(part)) for part in content)


def _build_mock_completion(req: ChatCompletionRequest) -> str:
    """Generate a deterministic mock reply from the last user message.

    Determinism matters for the fixture regression: ``pytest`` should
    be able to assert on the exact text. Real upstream forwarding
    takes over from TASK-002-5.
    """
    last_user = next(
        (m for m in reversed(req.messages) if m.role == "user"), None
    )
    if last_user is None:
        return (
            "[token-saver mock] No user message in the request — "
            "TASK-002-5 will forward this to a real provider."
        )
    preview = _content_chars(last_user.content)
    return (
        f"[token-saver mock] Received {preview} chars on model={req.model!r}. "
        "Real upstream forwarding lands in TASK-002-5."
    )


@router.post(
    "/v1/chat/completions",
    response_model=ChatCompletionResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "Bad request (e.g. stream=true not yet supported)"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        503: {"model": ErrorEnvelope, "description": "No provider available"},
    },
)
async def create_chat_completion(
    response: Response,
    body: ChatCompletionRequest,
) -> ChatCompletionResponse:
    """OpenAI-compatible chat completion endpoint.

    Validation: Pydantic enforces required fields and bounds. We add
    one explicit guard (``stream`` is not yet supported) so the 400 vs
    422 split is intentional, not accidental.
    """
    # Surface the mock flag at the HTTP layer — clients that strip or
    # transform the body can still observe it. Routers that proxy the
    # response downstream must preserve this header verbatim. Real
    # upstream forwarding in TASK-002-5 simply omits the header.
    response.headers[MOCK_HEADER] = MOCK_HEADER_VALUE

    if body.stream:
        # Streaming is a TASK-002-6 deliverable. Returning 400 with a
        # clear message keeps clients honest until SSE passthrough ships.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorEnvelope(
                error=ErrorBody(
                    message="stream=true is not supported yet (planned for TASK-002-6).",
                    type="invalid_request_error",
                    param="stream",
                    code="stream_not_supported",
                )
            ).model_dump(),
        )

    completion_text = _build_mock_completion(body)
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=int(time.time()),
        model=body.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=completion_text),
                finish_reason="stop",
            )
        ],
        usage=_estimate_tokens(body.messages, completion_text),
        routed_provider=None,  # TASK-002-5 fills this in.
    )
