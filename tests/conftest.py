"""Shared test fixtures for Donna.

Provides state machine config, state machine, and database fixtures
reusable across unit and integration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import (
    InvalidTransitionEntry,
    StateMachineConfig,
    TransitionEntry,
)
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def state_machine_config() -> StateMachineConfig:
    """Build a state machine config matching config/task_states.yaml."""
    return StateMachineConfig(
        states=[
            "backlog",
            "scheduled",
            "in_progress",
            "blocked",
            "waiting_input",
            "done",
            "cancelled",
        ],
        initial_state="backlog",
        transitions=[
            TransitionEntry(
                **{
                    "from": "backlog",
                    "to": "scheduled",
                    "trigger": "scheduler_assigns_slot",
                    "side_effects": [
                        "create_calendar_event",
                        "set_donna_managed_true",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "scheduled",
                    "to": "in_progress",
                    "trigger": "user_starts",
                    "side_effects": ["set_actual_start"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "scheduled",
                    "to": "backlog",
                    "trigger": "user_cancels",
                    "side_effects": [
                        "delete_calendar_event",
                        "increment_reschedule_count",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "done",
                    "trigger": "user_completes",
                    "side_effects": [
                        "set_completed_at",
                        "update_velocity_metrics",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "blocked",
                    "trigger": "blocker_reported",
                    "side_effects": [
                        "update_dependencies",
                        "log_blocking_reason",
                        "notify_dependent_tasks",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "scheduled",
                    "trigger": "user_reschedules",
                    "side_effects": [
                        "assign_new_slot",
                        "increment_reschedule_count",
                        "update_calendar_event",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "blocked",
                    "to": "scheduled",
                    "trigger": "blocker_resolved",
                    "side_effects": ["find_next_available_slot"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "blocked",
                    "to": "cancelled",
                    "trigger": "user_abandons",
                    "side_effects": ["flag_dependent_tasks"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "waiting_input",
                    "to": "scheduled",
                    "trigger": "info_provided",
                    "side_effects": [
                        "pm_agent_updates_task",
                        "scheduler_assigns_slot",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "waiting_input",
                    "to": "cancelled",
                    "trigger": "timeout",
                    "side_effects": ["notify_user", "archive_task"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "*",
                    "to": "cancelled",
                    "trigger": "user_explicitly_cancels",
                    "side_effects": [
                        "flag_dependent_tasks",
                        "delete_calendar_event_if_exists",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "done",
                    "to": "in_progress",
                    "trigger": "user_reopens",
                    "side_effects": ["clear_completed_at"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "cancelled",
                    "to": "backlog",
                    "trigger": "user_reopens_cancelled",
                    "side_effects": ["clear_cancelled_at"],
                }
            ),
        ],
        invalid_transitions=[
            InvalidTransitionEntry(
                **{
                    "from": "backlog",
                    "to": "done",
                    "reason": "Cannot complete without scheduling.",
                }
            ),
            InvalidTransitionEntry(
                **{
                    "from": "cancelled",
                    "to": "*",
                    "except": ["backlog"],
                    "reason": "Must re-open to backlog first.",
                }
            ),
            InvalidTransitionEntry(
                **{
                    "from": "done",
                    "to": "scheduled",
                    "reason": "Must go through in_progress first.",
                }
            ),
        ],
    )


@pytest.fixture
def state_machine(state_machine_config: StateMachineConfig) -> StateMachine:
    """Build a StateMachine from the shared config."""
    return StateMachine(state_machine_config)


# ---------------------------------------------------------------------------
# Admin API fixtures
# ---------------------------------------------------------------------------


def _make_cursor(*fetchall_rows: list, fetchone_val: tuple | None = None) -> AsyncMock:
    """Build an AsyncMock cursor with pre-configured return values."""
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=fetchall_rows[0] if fetchall_rows else [])
    cursor.fetchone = AsyncMock(return_value=fetchone_val)
    return cursor


@pytest.fixture
def mock_request() -> tuple[MagicMock, AsyncMock]:
    """Mock FastAPI Request with app.state.db.connection and config_dir.

    Returns (request, connection) so tests can configure connection.execute
    return values per-call.
    """
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request = MagicMock()
    request.app.state.db.connection = conn
    request.app.state.config_dir = "config"
    return request, conn


# ---------------------------------------------------------------------------
# Auth test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_test_app(tmp_path):
    """FastAPI app with the auth schema and a fake Immich client."""
    import ipaddress

    import aiosqlite
    from fastapi import FastAPI

    from donna.api.auth import dependencies as auth_deps
    from donna.api.auth.config import (
        AuthConfig,
        BootstrapSettings,
        DeviceTokenSettings,
        EmailSettings,
        IPGateConfig,
        ImmichSettings,
        RateLimit,
    )

    db_path = tmp_path / "auth_flow_test.db"
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE trusted_ips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            access_level TEXT, trust_duration TEXT,
            trusted_at DATETIME, expires_at DATETIME,
            verified_by TEXT, label TEXT, last_seen DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT DEFAULT 'web',
            revoked_at DATETIME, revoked_by TEXT, revoke_reason TEXT
        );
        CREATE TABLE verification_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            ip_address TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            trust_duration TEXT NOT NULL DEFAULT '30d'
        );
        CREATE TABLE ip_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            service TEXT, action TEXT, user_id TEXT
        );
        CREATE TABLE allowed_emails (
            email TEXT PRIMARY KEY,
            immich_user_id TEXT NOT NULL,
            name TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            synced_at DATETIME NOT NULL
        );
        CREATE TABLE users (
            donna_user_id TEXT PRIMARY KEY,
            immich_user_id TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login_at DATETIME
        );
        CREATE TABLE device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            token_lookup TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            label TEXT, user_agent TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME, last_seen_ip TEXT,
            expires_at DATETIME NOT NULL,
            revoked_at DATETIME, revoked_by TEXT
        );
        CREATE TABLE llm_gateway_callers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id TEXT NOT NULL UNIQUE,
            key_hash TEXT NOT NULL,
            monthly_budget_usd REAL NOT NULL DEFAULT 0.0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            revoked_at DATETIME, revoke_reason TEXT
        );
        INSERT INTO allowed_emails (email, immich_user_id, name, is_admin, synced_at)
        VALUES ('nick@example.com', 'imm_nick', 'Nick', 1, CURRENT_TIMESTAMP);
        """
    )
    await conn.commit()

    cfg = AuthConfig(
        ip_gate=IPGateConfig(
            default_trust_duration="30d",
            durations_allowed=["24h", "7d", "30d", "90d"],
            rate_limit_request_access=RateLimit(max=100, window_seconds=3600),
            rate_limit_verify=RateLimit(max=100, window_seconds=600),
        ),
        trusted_proxies=[ipaddress.ip_network("127.0.0.0/8")],
        internal_cidrs=[ipaddress.ip_network("172.18.0.0/16")],
        immich=ImmichSettings(
            internal_url="http://immich:2283",
            external_url="https://immich.example",
            admin_api_key_env="IMMICH_ADMIN_API_KEY",
            user_cache_ttl_seconds=60,
            allowlist_sync_interval_seconds=900,
            allowlist_stale_tolerance_seconds=86400,
        ),
        device_tokens=DeviceTokenSettings(
            sliding_window_days=90, absolute_max_days=365, max_per_user=10,
        ),
        email=EmailSettings(
            from_addr="donna@example",
            subject="Donna verify",
            verify_base_url="https://donna.example/auth/verify",
            token_expiry_minutes=15,
        ),
        bootstrap=BootstrapSettings(admin_email_env="DONNA_BOOTSTRAP_ADMIN_EMAIL"),
    )

    gmail_mock = AsyncMock()
    gmail_mock.create_draft.return_value = "draft1"
    gmail_mock.send_draft.return_value = True

    immich_mock = AsyncMock()

    app = FastAPI()
    app.state.auth_config = cfg
    app.state.auth_context = auth_deps.AuthContext(
        conn=conn, auth_config=cfg, immich_client=immich_mock,
    )
    app.state.gmail = gmail_mock

    yield app, conn, gmail_mock, immich_mock

    await conn.close()


@pytest.fixture
async def auth_test_app_with_admin(auth_test_app):
    """Admin-authorised variant of `auth_test_app`.

    Overrides the admin dependency to resolve to a fixed admin user_id so
    route handlers run without needing a full IP-gate + Immich-bearer
    round trip. The admin dep is tested separately in
    `test_auth_dependencies.py`.
    """
    from donna.api.auth.router_factory import _admin_dep

    app, conn, _gmail, _immich = auth_test_app
    app.dependency_overrides[_admin_dep] = lambda: "admin_user"
    yield app, conn


@pytest.fixture
async def auth_test_app_user_only(auth_test_app):
    """Non-admin variant of `auth_test_app`.

    Overrides the admin dependency to raise 403 so we can assert that
    admin-only routes reject non-admin callers.
    """
    from fastapi import HTTPException, status

    from donna.api.auth.router_factory import _admin_dep

    def _deny() -> str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_role_required"},
        )

    app, conn, _gmail, _immich = auth_test_app
    app.dependency_overrides[_admin_dep] = _deny
    yield app, conn


@pytest.fixture(autouse=True)
def _reset_default_tool_registry():
    """Clear DEFAULT_TOOL_REGISTRY between tests to prevent cross-test leakage."""
    from donna.skills.tools import DEFAULT_TOOL_REGISTRY
    yield
    DEFAULT_TOOL_REGISTRY.clear()


@pytest.fixture(autouse=True)
def _stub_calendar_client_builder(monkeypatch):
    """Stub GoogleCalendarClient construction in tests.

    Production wiring returns None when calendar credentials are absent,
    which leaves ``calendar_read`` unregistered. The boot-time
    ``CapabilityToolRegistryCheck`` then fails because seeded capabilities
    like ``generate_digest`` declare ``calendar_read`` as a dependency.
    In tests, no credentials exist — so return a MagicMock here so the
    tool registers and the check passes. Individual tests that want to
    exercise the "unregistered tool" code path can re-patch the builder
    inside the test body.
    """
    from unittest.mock import AsyncMock, MagicMock

    def _stub(_config_dir):
        c = MagicMock()
        c.list_events = AsyncMock(return_value=[])
        return c

    monkeypatch.setattr("donna.cli_wiring._try_build_calendar_client", _stub)
