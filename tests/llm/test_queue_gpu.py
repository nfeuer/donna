"""Tests for GPU-aware queue behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.llm.queue import LLMQueueWorker
from donna.llm.types import GatewayConfig, GpuConfig, Priority, QueueItem


def _make_worker(gpu_home: str = "qwen2.5:32b-instruct-q6_K") -> LLMQueueWorker:
    gpu_config = GpuConfig(home_model=gpu_home, restore_home_delay_s=0)
    config = GatewayConfig(gpu=gpu_config)
    ollama = AsyncMock()
    ollama.complete = AsyncMock(return_value=({"result": "ok"}, MagicMock(
        latency_ms=100, tokens_in=10, tokens_out=20, cost_usd=0.0,
    )))
    ollama.list_running = AsyncMock(return_value=["qwen2.5:32b-instruct-q6_K"])
    alerter = AsyncMock()
    # Make alerter.send_alert available
    alerter.send_alert = AsyncMock()
    rate_limiter = MagicMock()
    rate_limiter.get_all_usage = MagicMock(return_value={})

    worker = LLMQueueWorker(
        config=config, ollama=ollama,
        inv_logger=MagicMock(), alerter=alerter,
        rate_limiter=rate_limiter,
    )
    return worker


class TestModelAffinitySort:
    def test_pop_next_prefers_matching_model(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")

        loop = asyncio.new_event_loop()

        f1 = loop.create_future()
        item_vision = QueueItem(
            prompt="vision", model="ollama", max_tokens=100,
            json_mode=True, future=f1, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5-vl:7b",
            sequence=1,
        )
        f2 = loop.create_future()
        item_text = QueueItem(
            prompt="text", model="ollama", max_tokens=100,
            json_mode=True, future=f2, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5:32b-instruct-q6_K",
            sequence=2,
        )

        worker._internal.put_nowait(item_vision)
        worker._internal.put_nowait(item_text)

        popped = worker._pop_next()
        assert popped is not None
        assert popped.required_model == "qwen2.5:32b-instruct-q6_K"

        loop.close()

    def test_pop_next_respects_priority_over_affinity(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")

        loop = asyncio.new_event_loop()

        f1 = loop.create_future()
        item_critical = QueueItem(
            prompt="critical", model="ollama", max_tokens=100,
            json_mode=True, future=f1, is_internal=True,
            priority=Priority.CRITICAL, required_model="qwen2.5-vl:7b",
            sequence=1,
        )
        f2 = loop.create_future()
        item_normal = QueueItem(
            prompt="normal", model="ollama", max_tokens=100,
            json_mode=True, future=f2, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5:32b-instruct-q6_K",
            sequence=2,
        )

        worker._internal.put_nowait(item_critical)
        worker._internal.put_nowait(item_normal)

        popped = worker._pop_next()
        assert popped is not None
        assert popped.priority == Priority.CRITICAL

        loop.close()

    def test_none_required_model_matches_any(self):
        """Items with required_model=None should be treated as matching current model."""
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")

        loop = asyncio.new_event_loop()

        f1 = loop.create_future()
        item_needs_swap = QueueItem(
            prompt="swap", model="ollama", max_tokens=100,
            json_mode=True, future=f1, is_internal=True,
            priority=Priority.NORMAL, required_model="qwen2.5-vl:7b",
            sequence=1,
        )
        f2 = loop.create_future()
        item_any = QueueItem(
            prompt="any", model="ollama", max_tokens=100,
            json_mode=True, future=f2, is_internal=True,
            priority=Priority.NORMAL, required_model=None,
            sequence=2,
        )

        worker._internal.put_nowait(item_needs_swap)
        worker._internal.put_nowait(item_any)

        popped = worker._pop_next()
        assert popped is not None
        assert popped.required_model is None  # None matches current, preferred over swap

        loop.close()


class TestGpuStatus:
    def test_status_includes_gpu_section(self):
        worker = _make_worker()
        worker._gpu_tracker.record_loaded("qwen2.5:32b-instruct-q6_K")
        status = worker.get_status()
        assert "gpu" in status
        assert status["gpu"]["loaded_model"] == "qwen2.5:32b-instruct-q6_K"
        assert status["gpu"]["is_home"] is True
