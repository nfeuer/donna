"""Admin access panel: list/revoke/trust trusted IPs and device tokens."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from donna.api.routes import admin_access


def test_list_trusted_ips_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/ips")
    assert resp.status_code == 200
    assert "ips" in resp.json()


def test_revoke_ip_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    asyncio.get_event_loop().run_until_complete(
        conn.execute(
            "INSERT INTO trusted_ips (ip_address, status, source) "
            "VALUES ('1.2.3.4', 'trusted', 'web')"
        )
    )
    asyncio.get_event_loop().run_until_complete(conn.commit())

    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)
    resp = client.post("/admin/ips/1.2.3.4/revoke", json={"reason": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


def test_list_devices_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/devices")
    assert resp.status_code == 200


def test_non_admin_gets_403(auth_test_app_user_only):
    app, conn = auth_test_app_user_only
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/ips")
    assert resp.status_code == 403
