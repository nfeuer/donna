"""End-to-end: request access → verify → Immich login → authenticated task request."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.auth import verification_tokens


@pytest.mark.asyncio
async def test_full_flow_web_browser(auth_test_app):
    """Simulate a browser going through the full four-layer auth dance."""
    from fastapi.testclient import TestClient

    from donna.api.routes import auth_flow
    from donna.api.routes import tasks as tasks_route

    app, conn, gmail, immich_mock = auth_test_app

    # Tasks route uses app.state.db; stub it so we can exercise the
    # authenticated handler without standing up the full Database layer.
    db_mock = MagicMock()
    db_mock.list_tasks = AsyncMock(return_value=[])
    app.state.db = db_mock

    app.include_router(auth_flow.router, prefix="/auth")
    app.include_router(tasks_route.router, prefix="/tasks")

    client = TestClient(app)

    # 1. Unknown IP → /tasks rejected by the IP gate.
    resp = client.get("/tasks")
    assert resp.status_code in (401, 403)

    # 2. POST /auth/request-access with a known email → 202, email sent.
    resp = client.post("/auth/request-access", json={"email": "nick@example.com"})
    assert resp.status_code == 202
    assert gmail.send_draft.called

    # 3. Simulate clicking the link: create a verification token directly
    #    (TestClient's default client IP is "testclient").
    raw = await verification_tokens.create(
        conn, ip="testclient", email="nick@example.com", expiry_minutes=15
    )
    resp = client.post("/auth/verify", json={"token": raw})
    assert resp.status_code == 200

    # 4. Provision the Donna user row for nick.
    await conn.execute(
        "INSERT INTO users (donna_user_id, immich_user_id, email, role) "
        "VALUES ('nick', 'imm_nick', 'nick@example.com', 'user')"
    )
    await conn.commit()

    # 5. Mock Immich to return nick for the presented bearer.
    from donna.api.auth.immich import ImmichUser
    immich_mock.resolve.return_value = ImmichUser(
        immich_user_id="imm_nick",
        email="nick@example.com",
        name="Nick",
        is_admin=True,
    )

    # 6. With IP trusted + Immich bearer + provisioned user → /tasks succeeds.
    resp = client.get("/tasks", headers={"x-immich-token": "fake-but-mocked"})
    assert resp.status_code == 200
    assert resp.json()["tasks"] == []
