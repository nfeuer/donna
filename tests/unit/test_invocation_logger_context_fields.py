"""Tests that the invocation logger writes the new context-budget fields."""

from __future__ import annotations

import aiosqlite
import pytest

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata


@pytest.mark.asyncio
async def test_log_writes_estimated_tokens_and_overflow_flag(tmp_path) -> None:
    db_path = tmp_path / "inv.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                task_type TEXT,
                task_id TEXT,
                model_alias TEXT,
                model_actual TEXT,
                input_hash TEXT,
                latency_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                output TEXT,
                quality_score REAL,
                is_shadow INTEGER,
                eval_session_id TEXT,
                spot_check_queued INTEGER,
                user_id TEXT,
                queue_wait_ms INTEGER,
                interrupted INTEGER,
                chain_id TEXT,
                caller TEXT,
                estimated_tokens_in INTEGER,
                overflow_escalated INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await conn.commit()

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
            "SELECT estimated_tokens_in, overflow_escalated FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1480
        assert bool(row[1]) is False


@pytest.mark.asyncio
async def test_log_defaults_for_missing_context_fields(tmp_path) -> None:
    db_path = tmp_path / "inv2.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                task_type TEXT,
                task_id TEXT,
                model_alias TEXT,
                model_actual TEXT,
                input_hash TEXT,
                latency_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                output TEXT,
                quality_score REAL,
                is_shadow INTEGER,
                eval_session_id TEXT,
                spot_check_queued INTEGER,
                user_id TEXT,
                queue_wait_ms INTEGER,
                interrupted INTEGER,
                chain_id TEXT,
                caller TEXT,
                estimated_tokens_in INTEGER,
                overflow_escalated INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await conn.commit()

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
            "SELECT estimated_tokens_in, overflow_escalated FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None
        assert bool(row[1]) is False
