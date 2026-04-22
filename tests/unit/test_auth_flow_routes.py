"""Integration tests for /auth/* routes."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from donna.api.routes import auth_flow


def test_request_access_unknown_email_returns_202(auth_test_app):
    app, _conn, gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post("/auth/request-access", json={"email": "attacker@evil.com"})
    assert resp.status_code == 202
    gmail.send_draft.assert_not_called()


def test_request_access_known_email_sends_email(auth_test_app):
    app, _conn, gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post(
        "/auth/request-access",
        json={"email": "Nick@Example.com"},
    )
    assert resp.status_code == 202
    gmail.create_draft.assert_called_once()
    gmail.send_draft.assert_called_once()


def test_request_access_malformed_returns_202_no_send(auth_test_app):
    app, _conn, gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post("/auth/request-access", json={"email": "not-an-email"})
    assert resp.status_code == 202
    gmail.send_draft.assert_not_called()


def test_verify_marks_ip_trusted_and_burns_token(auth_test_app):
    """Full happy path: request -> verify -> IP is trusted."""
    from donna.api.auth import verification_tokens as vt

    app, conn, _gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    raw = asyncio.get_event_loop().run_until_complete(
        vt.create(conn, ip="testclient", email="nick@example.com", expiry_minutes=15)
    )

    resp = client.post("/auth/verify", json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["trusted"] is True

    resp2 = client.post("/auth/verify", json={"token": raw})
    assert resp2.status_code == 400


def test_verify_get_from_email_trusts_ip(auth_test_app):
    """Clicking the magic link (GET) trusts the IP and returns HTML."""
    from donna.api.auth import verification_tokens as vt

    app, conn, _gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    raw = asyncio.get_event_loop().run_until_complete(
        vt.create(conn, ip="testclient", email="nick@example.com", expiry_minutes=15)
    )

    resp = client.get(f"/auth/verify?token={raw}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "trusted" in resp.text.lower()

    resp2 = client.get(f"/auth/verify?token={raw}")
    assert resp2.status_code == 400


def test_status_reflects_ip_trust_state(auth_test_app):
    app, _conn, _gmail, _immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.get("/auth/status")
    assert resp.status_code == 200
    assert resp.json()["trusted"] is False
