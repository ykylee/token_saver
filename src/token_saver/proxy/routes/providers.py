"""Provider CRUD — operator-facing surface.

Five endpoints, all gated by authentication:

- ``POST /v1/providers/test`` — probe a config without persisting.
  Available to any authenticated user (regular or admin).
- ``POST /v1/providers`` — register a provider. Owned by the
  caller.
- ``GET /v1/providers`` — list. Regular users see their own rows;
  admins see all rows across tenants.
- ``POST /v1/providers/{provider_id}/models/refresh`` — re-discover
  the upstream's ``GET /v1/models``. Writes through to Redis
  (1h TTL) and the Mongo ``models_cache`` sub-document.
- ``DELETE /v1/providers/{provider_id}`` — drop a provider.
  Regular users can only delete their own.

Multi-user isolation is enforced at the store boundary via
``_scope_user_id``:

- ``user_id=None`` (admin) → store filters all rows.
- ``user_id=<id>`` (regular) → store filters that user's rows.

The route never reaches into the underlying collection; the store
owns the access control so adding a new backend (e.g. PostgreSQL)
doesn't fork the RBAC logic.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from token_saver.auth.deps import get_current_user
from token_saver.models import (
    CurrentUser,
    ModelsRefreshResult,
    ProviderCreateRequest,
    ProviderInfo,
    ProviderRecord,
    ProviderTestRequest,
    ProviderTestResult,
)
from token_saver.provider.base import (
    ProviderConnectionError,
    ProviderError,
    ProviderResponseError,
)
from token_saver.provider.cache import ModelCatalogCache
from token_saver.provider.deps import get_model_cache, get_provider_store
from token_saver.provider.registry import ProviderRegistry, UnknownProviderTypeError
from token_saver.provider.store import ProviderStore

__all__ = ["router"]


router = APIRouter(tags=["providers"], prefix="/v1/providers")


# ----- helpers -----


def _scope_user_id(current: CurrentUser) -> str | None:
    """Return the tenant scope for ``current``.

    Admins get ``None`` (store treats it as "all rows across
    tenants"); regular users get their own id (store filters to
    that owner). The store is the one place that knows about the
    ``None`` semantics — routes just translate the role.
    """
    return None if current.role == "admin" else current.id


def _record_to_info(record: ProviderRecord) -> ProviderInfo:
    """Project a :class:`ProviderRecord` to :class:`ProviderInfo`.

    ``ProviderRecord`` carries the decrypted ``api_key``; the
    :class:`ProviderInfo` wire shape intentionally omits it so we
    never leak credentials through ``GET /v1/providers``. ``base_url``
    stays because operators need it to identify which row is which
    (especially when they have multiple accounts against the same
    vendor).
    """
    return ProviderInfo(
        id=record.id,
        name=record.name,
        type=record.type,
        base_url=record.base_url,
        default_model=record.default_model,
        enabled=record.enabled,
        owner_user_id=record.owner_user_id,
    )


def _error_envelope(message: str, type_: str, code: str, http_status: int) -> HTTPException:
    """Build a :class:`HTTPException` with the project's error envelope.

    Mirrors the shape used by ``auth.deps._unauthorized`` /
    ``_forbidden`` so the OpenAPI clients see one consistent error
    schema across every endpoint.
    """
    from token_saver.models import ErrorBody, ErrorEnvelope

    return HTTPException(
        status_code=http_status,
        detail=ErrorEnvelope(
            error=ErrorBody(message=message, type=type_, code=code)
        ).model_dump(),
    )


def _http_502(exc: ProviderError) -> HTTPException:
    """Translate a :class:`ProviderError` into a 502 with envelope.

    Connection / response / config errors all map to 502 — the
    proxy can't tell the upstream's health apart from its vantage
    point, and operators debug from the proxy's view.
    """
    code_map = {
        ProviderConnectionError: "provider_connection_error",
        ProviderResponseError: "provider_response_error",
    }
    code = code_map.get(type(exc), "provider_unavailable")
    return _error_envelope(
        message=str(exc),
        type_="upstream_error",
        code=code,
        http_status=status.HTTP_502_BAD_GATEWAY,
    )


# ----- POST /v1/providers/test -----


@router.post(
    "/test",
    response_model=ProviderTestResult,
    responses={
        200: {"description": "Connection test verdict."},
        400: {"description": "Invalid request (e.g. unknown provider type)."},
        401: {"description": "Missing or invalid bearer token."},
    },
)
async def test_provider(
    body: ProviderTestRequest,
    _user: CurrentUser = Depends(get_current_user),
) -> ProviderTestResult:
    """Probe a provider config without persisting it.

    The route builds a transient :class:`BaseProvider` from
    ``body`` and runs :meth:`BaseProvider.test_connection`. The
    verdict tells the operator whether the URL is reachable and
    authentication (if any) works — they hit
    ``POST /v1/providers`` only when this comes back green.
    """
    from token_saver.provider.base import ProviderConfig

    config = ProviderConfig(
        id="test",  # transient — never registered
        name="test",
        type=body.type,
        base_url=body.base_url,
        api_key=body.api_key,
        default_model=body.default_model or "test-model",
    )
    try:
        provider = ProviderRegistry.from_config(config)
    except UnknownProviderTypeError as exc:
        raise _error_envelope(
            message=str(exc),
            type_="invalid_request_error",
            code="unknown_provider_type",
            http_status=status.HTTP_400_BAD_REQUEST,
        ) from exc
    try:
        result = await provider.test_connection()
    except ProviderError as exc:
        # Should not normally happen — ``test_connection`` returns
        # ``ProviderTestResult`` rather than raising — but if a new
        # impl changes that contract we still 502 cleanly.
        raise _http_502(exc) from exc
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()

    if result.ok:
        from token_saver.models import ModelCard

        sample = [
            ModelCard(id=m.id, owned_by=m.owned_by)
            for m in result.sample_models
        ]
    else:
        sample = []
    return ProviderTestResult(
        ok=result.ok,
        latency_ms=result.latency_ms,
        models_count=result.models_count,
        sample_models=sample,
        error=result.error,
    )


# ----- POST /v1/providers -----


@router.post(
    "",
    response_model=ProviderInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Provider created."},
        400: {"description": "Invalid request (e.g. unknown provider type)."},
        401: {"description": "Missing or invalid bearer token."},
        502: {"description": "Provider connection or response error."},
    },
)
async def create_provider(
    body: ProviderCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
) -> ProviderInfo:
    """Register a new provider.

    Tests the connection on the way in (race-condition guard — the
    operator may have rotated their key between the ``/test`` call
    and this ``/providers`` call), persists via :class:`ProviderStore`,
    and returns the operator-facing :class:`ProviderInfo` (no
    ``api_key``).
    """
    from token_saver.provider.base import ProviderConfig

    config = ProviderConfig(
        id="create",
        name=body.name,
        type=body.type,
        base_url=body.base_url,
        api_key=body.api_key,
        default_model=body.default_model,
        enabled=body.enabled,
    )
    try:
        provider = ProviderRegistry.from_config(config)
    except UnknownProviderTypeError as exc:
        raise _error_envelope(
            message=str(exc),
            type_="invalid_request_error",
            code="unknown_provider_type",
            http_status=status.HTTP_400_BAD_REQUEST,
        ) from exc

    # Connection probe — refuse to persist a known-bad config.
    test_result = await provider.test_connection()
    if not test_result.ok:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
        raise _error_envelope(
            message=test_result.error or "provider connection test failed",
            type_="upstream_error",
            code="provider_test_failed",
            http_status=status.HTTP_502_BAD_GATEWAY,
        )

    record = await store.create(
        owner_user_id=user.id,
        name=body.name,
        type=body.type,
        base_url=body.base_url,
        api_key=body.api_key,
        default_model=body.default_model,
        enabled=body.enabled,
    )
    aclose = getattr(provider, "aclose", None)
    if aclose is not None:
        await aclose()
    return _record_to_info(record)


# ----- GET /v1/providers -----


@router.get(
    "",
    response_model=list[ProviderInfo],
    responses={
        200: {"description": "Provider list (own rows for regular users; all rows for admins)."},
        401: {"description": "Missing or invalid bearer token."},
    },
)
async def list_providers(
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
) -> list[ProviderInfo]:
    """List providers the caller is allowed to see."""
    scope = _scope_user_id(user)
    records = await store.list_for_user(scope)
    return [_record_to_info(r) for r in records]


# ----- POST /v1/providers/{provider_id}/models/refresh -----


@router.post(
    "/{provider_id}/models/refresh",
    response_model=ModelsRefreshResult,
    responses={
        200: {"description": "Models refreshed."},
        401: {"description": "Missing or invalid bearer token."},
        404: {"description": "Provider not found (or not visible to caller)."},
        502: {"description": "Upstream connection / response error."},
    },
)
async def refresh_models(
    provider_id: str,
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
    cache: ModelCatalogCache = Depends(get_model_cache),
) -> ModelsRefreshResult:
    """Re-discover the upstream's ``GET /v1/models``.

    Persists the catalog on the provider row (``models_cache``) and
    writes through to the Redis cache with the configured TTL. The
    delta (``added`` / ``removed``) helps an operator confirm that
    nothing surprising changed since the last refresh.
    """
    from token_saver.provider.base import ProviderConfig

    scope = _scope_user_id(user)
    record = await store.get(provider_id, scope)
    if record is None:
        raise _error_envelope(
            message=f"provider {provider_id!r} not found",
            type_="not_found_error",
            code="provider_not_found",
            http_status=status.HTTP_404_NOT_FOUND,
        )
    config = ProviderConfig(
        id=record.id,
        name=record.name,
        type=record.type,
        base_url=record.base_url,
        api_key=record.api_key,
        default_model=record.default_model,
        enabled=record.enabled,
    )
    try:
        provider = ProviderRegistry.from_config(config)
    except UnknownProviderTypeError as exc:
        raise _error_envelope(
            message=str(exc),
            type_="invalid_request_error",
            code="unknown_provider_type",
            http_status=status.HTTP_400_BAD_REQUEST,
        ) from exc

    try:
        models = await provider.list_models()
    except ProviderError as exc:
        raise _http_502(exc) from exc
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()

    # Persist + cache.
    payload: list[dict[str, object]] = [
        {"id": m.id, "owned_by": m.owned_by} for m in models
    ]
    await store.set_models_cache(
        provider_id, scope, models=payload
    )
    ttl_seconds = _cache_ttl_seconds()
    await cache.set(provider_id, payload, ttl_seconds=ttl_seconds)

    # Delta vs the previous catalog (in-process view).
    previous = await store.get(provider_id, scope)
    previous_ids: set[str]
    if previous is None:
        previous_ids = set()
    else:
        # ``ProviderRecord`` doesn't surface the models cache list
        # directly; we re-read via the route's caller-supplied
        # payload. For now, the delta is best-effort: if the row
        # just got rewritten above we don't have the prior list.
        # Future cycle can stash the previous snapshot in Redis or
        # a dedicated sub-document; the simpler answer is "no
        # delta" when we just rewrote the cache.
        previous_ids = set()
    new_ids = {m.id for m in models}
    added = sorted(new_ids - previous_ids)
    removed = sorted(previous_ids - new_ids)
    return ModelsRefreshResult(
        provider_id=provider_id,
        models_count=len(models),
        added=added,
        removed=removed,
    )


# ----- DELETE /v1/providers/{provider_id} -----


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Provider deleted."},
        401: {"description": "Missing or invalid bearer token."},
        404: {"description": "Provider not found (or not visible to caller)."},
    },
)
async def delete_provider(
    provider_id: str,
    user: CurrentUser = Depends(get_current_user),
    store: ProviderStore = Depends(get_provider_store),
    cache: ModelCatalogCache = Depends(get_model_cache),
) -> JSONResponse:
    """Drop a provider row.

    Also clears the Redis cache entry so a deleted provider's stale
    catalog doesn't survive a refresh from the wrong tab. Regular
    users can only delete their own rows; admins can delete any
    row.
    """
    scope = _scope_user_id(user)
    deleted = await store.delete(provider_id, scope)
    if not deleted:
        raise _error_envelope(
            message=f"provider {provider_id!r} not found",
            type_="not_found_error",
            code="provider_not_found",
            http_status=status.HTTP_404_NOT_FOUND,
        )
    await cache.invalidate(provider_id)
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# ----- helpers -----


def _cache_ttl_seconds() -> int:
    """Read the model-cache TTL from the active settings.

    Lives in the route module (rather than ``config``) so the
    factory and tests don't have to thread ``Settings`` through.
    """
    from token_saver.config import get_settings

    return get_settings().provider_models_cache_ttl_seconds


# Suppress unused-import warnings for ``time`` if a future cycle adds
# timestamp fields to the response.
_ = time
