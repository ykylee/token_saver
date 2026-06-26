"""End-to-end tests for the admin surface.

TASK-002-3 promotes ``/admin/health`` to **admin-only**. The legacy
``/healthz`` probe stays open so docker-compose healthchecks keep
working without a token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from token_saver import __version__


def test_health_requires_admin_token(client: TestClient) -> None:
    """No bearer → 401."""
    resp = client.get("/admin/health")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_health_rejects_user_role(client: TestClient, user_auth_header: dict[str, str]) -> None:
    """Authenticated but non-admin → 403."""
    resp = client.get("/admin/health", headers=user_auth_header)
    assert resp.status_code == 403
    body = resp.json()
    assert body["detail"]["error"]["code"] == "forbidden"


def test_health_allows_admin(
    client: TestClient, admin_auth_header: dict[str, str]
) -> None:
    """Admin bearer → 200 + payload."""
    resp = client.get("/admin/health", headers=admin_auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["schema_version"] >= 1
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_healthz_legacy_probe_still_works(client: TestClient) -> None:
    """``/healthz`` stays open so container probes don't need a token."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
