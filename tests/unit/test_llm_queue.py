"""Tests for the LLM queue worker — ordering, preemption, chain handling."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.llm.alerter import GatewayAlerter
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import GatewayConfig, Priority, QueueItem
from donna.models.types import CompletionMetadata


def _make_config(**overrides) -> GatewayConfig:
    defaults = {
        "active_hours_start": 0,
        "active_hours_end": 24,  # always active for tests
        "schedule_drain_minutes": 2,
        "max_external_depth": 20,
        "max_interrupt_count": 3,
    }
    defaults.update(overrides)
    return GatewayConfig(**defaults)


def _make_meta() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=100,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        model_actual="ollama/test",
    )


def _make_ollama() -> AsyncMock:
    ollama = AsyncMock()
    ollama.complete = AsyncMock(return_value=({"result": "ok"}, _make_meta()))
    return ollama


class TestQueueOrdering:
    async def test_internal_before_external(self) -> None:
        """Internal items are processed before external items."""
        config = _make_config()
        ollama = _make_ollama()
        worker = LLMQueueWorker(
            config=config,
            ollama=ollama,
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        order: list[str] = []

        async def mock_complete(prompt, model, **kwargs):
            order.append(prompt)
            return {"result": "ok"}, _make_meta()

        ollama.complete = mock_complete

        # Enqueue external first, then internal
        await worker.enqueue_external(
            prompt="external", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )
        await worker.enqueue_internal(
            prompt="internal", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.NORMAL,
        )

        # Process two items
        await worker.process_one()
        await worker.process_one()

        assert order == ["internal", "external"]

    async def test_critical_before_normal_before_background(self) -> None:
        config = _make_config()
        ollama = _make_ollama()
        worker = LLMQueueWorker(
            config=config,
            ollama=ollama,
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        order: list[str] = []

        async def mock_complete(prompt, model, **kwargs):
            order.append(prompt)
            return {"result": "ok"}, _make_meta()

        ollama.complete = mock_complete

        # Enqueue in reverse priority order
        await worker.enqueue_internal(
            prompt="bg", model="m", max_tokens=100,
            json_mode=True, task_type="generate_nudge", priority=Priority.BACKGROUND,
        )
        await worker.enqueue_internal(
            prompt="crit", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.CRITICAL,
        )
        await worker.enqueue_internal(
            prompt="norm", model="m", max_tokens=100,
            json_mode=True, task_type="generate_digest", priority=Priority.NORMAL,
        )

        await worker.process_one()
        await worker.process_one()
        await worker.process_one()

        assert order == ["crit", "norm", "bg"]


class TestQueueStatus:
    async def test_status_shows_queue_depths(self) -> None:
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        await worker.enqueue_internal(
            prompt="p", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.CRITICAL,
        )
        await worker.enqueue_external(
            prompt="p", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        status = worker.get_status()
        assert status["internal_queue"]["pending"] == 1
        assert status["external_queue"]["pending"] == 1


class TestExternalQueueLimit:
    async def test_rejects_when_full(self) -> None:
        config = _make_config(max_external_depth=2)
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        await worker.enqueue_external(
            prompt="1", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )
        await worker.enqueue_external(
            prompt="2", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        with pytest.raises(Exception, match="queue full"):
            await worker.enqueue_external(
                prompt="3", model="m", max_tokens=100,
                json_mode=True, caller="test", allow_cloud=False,
            )
