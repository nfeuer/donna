"""Slice 20 tests for the ``/donna_submit`` slash command.

Validates the slash-command wrapper that delegates to
:func:`donna.cost.escalation_submit_service.apply_submission`.
The test suite exercises the validation matrix directly through
:func:`donna.integrations.discord_submit_command._handle_submit`
so we don't need a live Discord client.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import PromptDeliveryConfig
from donna.integrations import discord_submit_command

_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT
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
    escalation_request_id INTEGER REFERENCES escalation_request(id)
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "submit.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


async def _seed(conn: aiosqlite.Connection, *, correlation_id: str = "cid-x") -> int:
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_id, task_type,
            estimate_usd, daily_remaining_usd, offered_modes,
            iteration, status, created_at, priority,
            delivery_status, delivery_attempts, mode
        )
        VALUES (
            'nick', ?, 'task-1', 'chat_escalation',
            7.5, 1.0, '["chat","pause","cancel"]',
            1, 'resolved', '2026-05-06T00:00:00+00:00', 2,
            'sent', 1, 'chat'
        )
        """,
        (correlation_id,),
    )
    await conn.commit()
    new_id = cur.lastrowid
    assert new_id is not None
    return int(new_id)


def _interaction(*, user_id: int = 4242) -> MagicMock:
    inter = MagicMock()
    inter.user.id = user_id
    inter.response.send_message = AsyncMock()
    return inter


def _config(**overrides) -> PromptDeliveryConfig:
    return PromptDeliveryConfig(**overrides)


class TestValidation:
    async def test_short_answer_rejected(self, conn: aiosqlite.Connection) -> None:
        await _seed(conn)
        inter = _interaction()
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-x",
            answer="too short",
            conn=conn,
            config=_config(chat_min_answer_chars=50),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        inter.response.send_message.assert_awaited_once()
        msg, kwargs = inter.response.send_message.call_args
        assert "too short" in msg[0]
        assert kwargs["ephemeral"] is True

    async def test_long_answer_redirected_to_dashboard(
        self, conn: aiosqlite.Connection
    ) -> None:
        await _seed(conn)
        inter = _interaction()
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-x",
            answer="x" * 3500,
            conn=conn,
            config=_config(slash_command_max_chars=3000),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        inter.response.send_message.assert_awaited_once()
        msg = inter.response.send_message.call_args[0][0]
        assert "long" in msg.lower() or "dashboard" in msg.lower()

    async def test_owner_mismatch_rejected(self, conn: aiosqlite.Connection) -> None:
        await _seed(conn)
        inter = _interaction(user_id=9999)
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-x",
            answer="x" * 60,
            conn=conn,
            config=_config(),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        msg = inter.response.send_message.call_args[0][0]
        assert "owner" in msg.lower()

    async def test_owner_check_skipped_when_unconfigured(
        self, conn: aiosqlite.Connection
    ) -> None:
        await _seed(conn)
        inter = _interaction(user_id=9999)
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-x",
            answer="x" * 60,
            conn=conn,
            config=_config(),
            iteration_limit=3,
            owner_discord_id=None,
        )
        msg = inter.response.send_message.call_args[0][0]
        assert "Submitted" in msg

    async def test_correlation_not_found(
        self, conn: aiosqlite.Connection
    ) -> None:
        inter = _interaction()
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="missing",
            answer="x" * 60,
            conn=conn,
            config=_config(),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        msg = inter.response.send_message.call_args[0][0]
        assert "No escalation matches" in msg


class TestSuccessPath:
    async def test_valid_answer_persists_and_replies_success(
        self, conn: aiosqlite.Connection
    ) -> None:
        await _seed(conn)
        inter = _interaction()
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-x",
            answer="x" * 100,
            conn=conn,
            config=_config(),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        msg = inter.response.send_message.call_args[0][0]
        assert "Submitted" in msg

        cur = await conn.execute(
            "SELECT status, mode, result FROM escalation_request "
            "WHERE correlation_id = 'cid-x'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "submitted"
        assert row[1] == "chat"
        payload = json.loads(row[2])
        assert payload["mode"] == "chat"
        assert payload["answer"].startswith("x")

    async def test_iteration_cap_surfaces_helpful_message(
        self, conn: aiosqlite.Connection
    ) -> None:
        # Pre-seed a row at iteration=3, status=failed.
        await conn.execute(
            """
            INSERT INTO escalation_request (
                user_id, correlation_id, task_id, task_type,
                estimate_usd, daily_remaining_usd, offered_modes,
                iteration, status, created_at, priority,
                delivery_status, delivery_attempts, mode
            )
            VALUES (
                'nick', 'cid-cap', 'task-1', 'chat_escalation',
                7.5, 1.0, '["chat","pause","cancel"]',
                3, 'failed', '2026-05-06T00:00:00+00:00', 2,
                'sent', 1, 'chat'
            )
            """,
        )
        await conn.commit()

        inter = _interaction()
        await discord_submit_command._handle_submit(
            interaction=inter,
            correlation_id="cid-cap",
            answer="x" * 60,
            conn=conn,
            config=_config(),
            iteration_limit=3,
            owner_discord_id=4242,
        )
        msg = inter.response.send_message.call_args[0][0]
        assert "iteration cap" in msg.lower()
