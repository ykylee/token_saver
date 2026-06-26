"""FastAPI dependencies for the provider subsystem.

Mirror of :mod:`auth.deps`: pull handles off ``app.state`` so tests
can swap implementations by setting different attributes on a fresh
app instance.
"""

from __future__ import annotations

from fastapi import Request

from token_saver.provider.registry import ProviderRegistry

__all__ = ["get_provider_registry"]


async def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the process-wide provider registry.

    Raises ``RuntimeError`` if the app was created without wiring
    one — that's a programmer error and should fail loudly so it
    surfaces at first request, not as a confusing 503.
    """
    registry = getattr(request.app.state, "provider_registry", None)
    if registry is None:
        raise RuntimeError(
            "provider_registry is not initialised on app.state. "
            "Did create_app() skip the provider wiring?"
        )
    return registry  # type: ignore[no-any-return]
