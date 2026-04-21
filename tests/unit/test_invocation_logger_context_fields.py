"""Tests that the invocation logger writes the new context-budget fields."""

from __future__ import annotations

import aiosqlite
import pytest

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata


async def _create_invocation_log_table(conn: aiosqlite.Connection) -> None:
    """Create invocation_log matching the production schema."""
    await conn.execute(
        """CREATE TABLE invocation_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            task_type TEXT NOT NULL,
            task_id TEXT,
            model_alias TEXT NOT NULL,
            model_actual TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            output TEXT,
            quality_score REAL,
            is_shadow INTEGER NOT NULL,
            eval_session_id TEXT,
            spot_check_queued INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            queue_wait_ms INTEGER,
            interrupted INTEGER NOT NULL DEFAULT 0,
            chain_id TEXT,
            caller TEXT,
            estimated_tokens_in INTEGER,
            overflow_escalated INTEGER NOT NULL DEFAULT 0,
            skill_id TEXT
        )"""
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_log_writes_estimated_tokens_and_overflow_flag(tmp_path) -> None:
    db_path = tmp_path / "inv.db"
    async with aiosqlite.connect(db_path) as conn:
        await _create_invocation_log_table(conn)

        logger = InvocationLogger(conn)
        inv_id = await logger.log(
            InvocationMetadata(
                task_type="generate_nudge",
                model_alias="local_parser",
                model_actual="ollama/qwen2.5:32b-instruct-q6_K",
                input_hash="abc",
                latency_ms=100,
                tokens_in=1500,
                tokens_out=50,
                cost_usd=0.0,
                user_id="nick",
                estimated_tokens_in=1480,
                overflow_escalated=False,
            )
        )

        cursor = await conn.execute(
            "SELECT estimated_tokens_in, overflow_escalated, tokens_in, latency_ms, model_alias FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1480
        assert bool(row[1]) is False
        assert row[2] == 1500
        assert row[3] == 100
        assert row[4] == "local_parser"


@pytest.mark.asyncio
async def test_log_defaults_for_missing_context_fields(tmp_path) -> None:
    db_path = tmp_path / "inv2.db"
    async with aiosqlite.connect(db_path) as conn:
        await _create_invocation_log_table(conn)

        logger = InvocationLogger(conn)
        inv_id = await logger.log(
            InvocationMetadata(
                task_type="parse_task",
                model_alias="parser",
                model_actual="anthropic/claude-sonnet-4-20250514",
                input_hash="def",
                latency_ms=200,
                tokens_in=900,
                tokens_out=120,
                cost_usd=0.004,
                user_id="nick",
            )
        )

        cursor = await conn.execute(
            "SELECT estimated_tokens_in, overflow_escalated, tokens_in, latency_ms, model_alias FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None
        assert bool(row[1]) is False
        assert row[2] == 900
        assert row[3] == 200
        assert row[4] == "parser"
