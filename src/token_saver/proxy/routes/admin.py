"""Admin endpoints — operator surface.

TASK-002-2 ships ``/admin/health`` only. Future cycles add audit
log access and user management once the auth layer lands (TASK-002-3+).

The health response intentionally omits any per-user detail — admin
RBAC is the gate, but the response shape stays tenant-blind so we
can front it with a load-balancer probe without auth in the loop.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from token_saver import __version__
from token_saver.__version__ import SCHEMA_VERSION
from token_saver.auth.deps import require_admin
from token_saver.models import CurrentUser, HealthResponse

__all__ = ["router"]

router = APIRouter(tags=["admin"], prefix="/admin", dependencies=[Depends(require_admin)])

# Captured at import time — close enough for uptime. A real deployment
# would set this in ``create_app`` so it tracks the FastAPI process
# rather than the module-import moment.
_BOOT_TS: float = time.time()


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        200: {"description": "Process liveness + version probe (admin only)."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "Authenticated but not an admin."},
    },
)
async def health(_admin: CurrentUser = Depends(require_admin)) -> HealthResponse:
    """Liveness probe.

    RBAC: ``admin`` role required (architecture §5.2). Container
    healthchecks continue to use ``/healthz`` (no auth). This probe
    is the operator surface — it surfaces the version + schema
    version so a probe can detect drift between deployed binaries.
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        schema_version=SCHEMA_VERSION,
        uptime_seconds=time.time() - _BOOT_TS,
    )
