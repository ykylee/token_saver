"""End-to-end tests for the admin surface.

TASK-002-2 ships ``/admin/health`` only. The RBAC layer that will
gate it for non-admin callers lands in TASK-002-3; for now the route
is open so the contract is exercised.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from token_saver import __version__


def test_health_returns_ok(client: TestClient) -> None:
    """Health probe responds 200 with ``status=ok`` and the package version."""
    resp = client.get("/admin/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["schema_version"] >= 1
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["uptime_seconds"] >= 0


def test_healthz_legacy_probe_still_works(client: TestClient) -> None:
    """The legacy ``/healthz`` (container probe) survives the route split."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
