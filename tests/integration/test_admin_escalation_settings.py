"""Slice 23 — integration tests for ``/admin/escalation-settings``.

Spins up a real aiosqlite connection, mounts the FastAPI router with the
admin auth dependency stubbed, and exercises GET / PUT round-trips
through ``httpx.ASGITransport``. Mirrors the slice 19 pattern in
:mod:`tests.integration.test_admin_escalations`.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from donna.api.auth.router_factory import _admin_dep
from donna.api.routes import admin_escalation_settings
from donna.config import (
    BudgetExtensionConfig,
    ManualEscalationConfig,
    ManualEscalationModesConfig,
    ManualEscalationTaskTypeConfig,
    ManualEscalationTriggersConfig,
    TaskTypeEntry,
    TaskTypesConfig,
)

_SCHEMA = """
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
"""


def _make_yaml_config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=10.0,
            hard_monthly_ceiling_usd=150.0,
        ),
        triggers=ManualEscalationTriggersConfig(task_approval_threshold_usd=5.0),
    )


def _make_task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "chat_escalation": TaskTypeEntry(
                description="x",
                model="parser",
                prompt_template="prompts/x.md",
                output_schema="schemas/x.json",
                manual_escalation=ManualEscalationTaskTypeConfig(mode="chat"),
            ),
            "skill_auto_draft": TaskTypeEntry(
                description="y",
                model="reasoner",
                prompt_template="prompts/y.md",
                output_schema="schemas/y.json",
                manual_escalation=ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={"skill": "skills/{name}/**"},
                    reference_module="skills/parse_task/skill.yaml",
                ),
            ),
            "no_manual": TaskTypeEntry(
                description="z",
                model="parser",
                prompt_template="prompts/z.md",
                output_schema="schemas/z.json",
            ),
        }
    )


@pytest.fixture
async def app_and_conn(tmp_path: Path):
    db_path = tmp_path / "esc_settings.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(_SCHEMA)
    await conn.commit()

    app = FastAPI()
    app.state.db = type("DB", (), {"connection": conn})()
    app.state.manual_escalation_config = _make_yaml_config()
    app.state.task_types_config = _make_task_types_config()
    app.include_router(admin_escalation_settings.router, prefix="/admin")
    app.dependency_overrides[_admin_dep] = lambda: "admin"

    yield app, conn

    await conn.close()


@pytest.fixture
async def client(app_and_conn):
    app, _conn = app_and_conn
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


class TestList:
    async def test_returns_yaml_defaults_when_no_overrides(
        self, client: AsyncClient
    ) -> None:
        r = await client.get("/admin/escalation-settings")
        assert r.status_code == 200
        body = r.json()
        keys = {s["key"] for s in body["settings"]}
        assert "manual_escalation.enabled" in keys
        assert "manual_escalation.modes.chat.enabled" in keys
        assert "manual_escalation.modes.claude_code.enabled" in keys
        assert "manual_escalation.budget_extension.enabled" in keys
        assert (
            "manual_escalation.budget_extension.max_daily_extension_usd" in keys
        )
        # No overrides yet, so updated_at is None and value == default.
        for s in body["settings"]:
            assert s["updated_at"] is None
            assert s["value"] == s["default"]

    async def test_renders_grid_only_for_overridable_task_types(
        self, client: AsyncClient
    ) -> None:
        r = await client.get("/admin/escalation-settings")
        body = r.json()
        names = sorted(row["task_type"] for row in body["task_type_overrides"])
        # "no_manual" has no manual_escalation block — must be omitted.
        assert names == ["chat_escalation", "skill_auto_draft"]
        for row in body["task_type_overrides"]:
            assert row["value"] == "auto"
            assert row["default"] == "auto"
            assert row["updated_at"] is None

    async def test_constraints_carry_slider_cap(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/admin/escalation-settings")).json()
        cap = body["constraints"]["max_daily_extension_cap_usd"]
        assert cap > 0
        assert cap <= 150.0  # ceiling
        # Exposes both the ceiling and the days_left for the UI hint.
        basis = body["constraints"]["max_daily_extension_cap_basis"]
        assert basis["hard_monthly_ceiling_usd"] == 150.0
        assert basis["days_left_in_month"] >= 1
        assert sorted(body["constraints"]["task_type_override_values"]) == [
            "auto",
            "disabled",
            "force_api",
            "force_manual",
        ]


# ---------------------------------------------------------------------------
# PUT — top-level
# ---------------------------------------------------------------------------


class TestPutToggle:
    async def test_first_write_succeeds_and_persists(
        self, client: AsyncClient, app_and_conn
    ) -> None:
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.enabled",
            json={"value": False, "expected_updated_at": None},
        )
        assert r.status_code == 200
        assert r.json()["value"] is False

        body = (await client.get("/admin/escalation-settings")).json()
        master = next(
            s for s in body["settings"] if s["key"] == "manual_escalation.enabled"
        )
        assert master["value"] is False
        assert master["updated_by"] == "admin"
        assert master["updated_at"] is not None

    async def test_audit_log_row_written(
        self, client: AsyncClient, app_and_conn
    ) -> None:
        _app, conn = app_and_conn
        await client.put(
            "/admin/escalation-settings/manual_escalation.enabled",
            json={"value": False, "expected_updated_at": None},
        )
        cur = await conn.execute(
            "SELECT task_type, output FROM invocation_log "
            "WHERE task_type = 'escalation_lifecycle'"
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        import json as _json

        payload = _json.loads(rows[0][1])
        assert payload["event"] == "dashboard_setting_changed"
        assert payload["key"] == "manual_escalation.enabled"
        assert payload["value"] is False

    async def test_optimistic_lock_returns_409(
        self, client: AsyncClient
    ) -> None:
        # First write — no token expected.
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.enabled",
            json={"value": False, "expected_updated_at": None},
        )
        assert r.status_code == 200
        # Stale-token write must 409 with the live state.
        r2 = await client.put(
            "/admin/escalation-settings/manual_escalation.enabled",
            json={
                "value": True,
                "expected_updated_at": "1999-01-01T00:00:00+00:00",
            },
        )
        assert r2.status_code == 409
        detail = r2.json()["detail"]
        assert detail["error"] == "version_mismatch"
        assert detail["current_value"] is False
        assert "current_updated_at" in detail
        assert detail["current_updated_by"] == "admin"

    async def test_unknown_key_404(self, client: AsyncClient) -> None:
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.bogus",
            json={"value": True, "expected_updated_at": None},
        )
        assert r.status_code == 404

    async def test_invalid_value_type_422(self, client: AsyncClient) -> None:
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.enabled",
            json={"value": "yes", "expected_updated_at": None},
        )
        assert r.status_code == 422

    async def test_slider_above_cap_422(self, client: AsyncClient) -> None:
        # 150 / days_in_month is well below 9999.
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.budget_extension.max_daily_extension_usd",
            json={"value": 9999, "expected_updated_at": None},
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["error"] == "exceeds_monthly_ceiling"

    async def test_slider_within_cap_succeeds(
        self, client: AsyncClient
    ) -> None:
        # The cap is 150/days_in_month — pick something safely below.
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.budget_extension.max_daily_extension_usd",
            json={"value": 1.5, "expected_updated_at": None},
        )
        assert r.status_code == 200
        assert r.json()["value"] == 1.5

    async def test_path_routes_block_task_type_endpoint(
        self, client: AsyncClient
    ) -> None:
        """Per-task-type keys are not writeable via the top-level PUT."""
        r = await client.put(
            "/admin/escalation-settings/manual_escalation.task_types.chat_escalation.override",
            json={"value": "force_api", "expected_updated_at": None},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "use_dedicated_task_type_endpoint"


# ---------------------------------------------------------------------------
# PUT — per-task-type override
# ---------------------------------------------------------------------------


class TestPutTaskType:
    async def test_writes_override(self, client: AsyncClient) -> None:
        r = await client.put(
            "/admin/escalation-settings/task-types/chat_escalation",
            json={"value": "force_api", "expected_updated_at": None},
        )
        assert r.status_code == 200
        assert r.json()["value"] == "force_api"

        body = (await client.get("/admin/escalation-settings")).json()
        row = next(
            x
            for x in body["task_type_overrides"]
            if x["task_type"] == "chat_escalation"
        )
        assert row["value"] == "force_api"
        assert row["updated_by"] == "admin"

    async def test_rejects_task_type_without_manual_block(
        self, client: AsyncClient
    ) -> None:
        r = await client.put(
            "/admin/escalation-settings/task-types/no_manual",
            json={"value": "force_api", "expected_updated_at": None},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "task_type_not_overridable"

    async def test_rejects_unknown_value(self, client: AsyncClient) -> None:
        r = await client.put(
            "/admin/escalation-settings/task-types/chat_escalation",
            json={"value": "nope", "expected_updated_at": None},
        )
        assert r.status_code == 422

    async def test_optimistic_lock_returns_409(self, client: AsyncClient) -> None:
        await client.put(
            "/admin/escalation-settings/task-types/chat_escalation",
            json={"value": "force_api", "expected_updated_at": None},
        )
        r = await client.put(
            "/admin/escalation-settings/task-types/chat_escalation",
            json={
                "value": "disabled",
                "expected_updated_at": "1999-01-01T00:00:00+00:00",
            },
        )
        assert r.status_code == 409
        assert r.json()["detail"]["current_value"] == "force_api"


# ---------------------------------------------------------------------------
# Degraded-config behaviour and legacy-alias surfacing
# ---------------------------------------------------------------------------


@pytest.fixture
async def degraded_app_and_conn(tmp_path: Path):
    """App with manual_escalation_config=None (YAML failed to load)."""
    db_path = tmp_path / "esc_settings_degraded.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(_SCHEMA)
    await conn.commit()

    app = FastAPI()
    app.state.db = type("DB", (), {"connection": conn})()
    app.state.manual_escalation_config = None
    app.state.task_types_config = None
    app.include_router(admin_escalation_settings.router, prefix="/admin")
    app.dependency_overrides[_admin_dep] = lambda: "admin"

    yield app, conn

    await conn.close()


@pytest.fixture
async def degraded_client(degraded_app_and_conn):
    app, _conn = degraded_app_and_conn
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestDegradedConfig:
    async def test_get_returns_503_when_config_missing(
        self, degraded_client: AsyncClient
    ) -> None:
        r = await degraded_client.get("/admin/escalation-settings")
        assert r.status_code == 503
        assert (
            r.json()["detail"]["error"] == "manual_escalation_config_unavailable"
        )

    async def test_put_slider_503_when_config_missing(
        self, degraded_client: AsyncClient
    ) -> None:
        r = await degraded_client.put(
            "/admin/escalation-settings/manual_escalation.budget_extension.max_daily_extension_usd",
            json={"value": 5.0, "expected_updated_at": None},
        )
        assert r.status_code == 503
        assert (
            r.json()["detail"]["error"] == "manual_escalation_config_unavailable"
        )

    async def test_put_task_type_503_when_config_missing(
        self, degraded_client: AsyncClient
    ) -> None:
        r = await degraded_client.put(
            "/admin/escalation-settings/task-types/chat_escalation",
            json={"value": "force_api", "expected_updated_at": None},
        )
        assert r.status_code == 503
        assert (
            r.json()["detail"]["error"] == "task_types_config_unavailable"
        )


class TestLegacyAliasSurfacing:
    async def test_legacy_row_appears_in_get_response(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        """Slice 17/18/21 wrote two keys without the ``manual_escalation.``
        prefix. The GET response must surface those rows under the
        canonical key so an upgraded deployment does not appear to have
        lost its overrides.
        """
        _app, conn = app_and_conn
        # Insert directly under the legacy alias.
        await conn.execute(
            """
            INSERT INTO dashboard_setting (key, value, updated_at, updated_by)
            VALUES ('modes.claude_code.enabled', 'false',
                    '2026-04-01T00:00:00+00:00', 'legacy_boot')
            """
        )
        await conn.commit()

        body = (await client.get("/admin/escalation-settings")).json()
        cc = next(
            s
            for s in body["settings"]
            if s["key"] == "manual_escalation.modes.claude_code.enabled"
        )
        assert cc["value"] is False
        assert cc["updated_by"] == "legacy_boot"
        assert cc["updated_at"] == "2026-04-01T00:00:00+00:00"
