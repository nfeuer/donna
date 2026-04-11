# tests/unit/test_llm_gateway.py — full replacement
"""Unit tests for the LLM gateway routes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.api.routes.llm import (
    CompletionRequest,
    CompletionResponse,
    llm_completion,
    llm_health,
    llm_models,
    llm_queue_status,
)
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import GatewayConfig
from donna.logging.invocation_logger import InvocationMetadata
from donna.models.types import CompletionMetadata


def _make_request(
    *,
    ollama: AsyncMock | None = None,
    queue: MagicMock | None = None,
    gateway_config: GatewayConfig | None = None,
) -> MagicMock:
    request = MagicMock()
    request.app.state.ollama = ollama
    request.app.state.llm_queue = queue
    request.app.state.llm_gateway_config = gateway_config or GatewayConfig()
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request.app.state.db.connection = conn
    return request


def _make_meta() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=500, tokens_in=100, tokens_out=50,
        cost_usd=0.001, model_actual="ollama/test",
    )


def test_invocation_metadata_has_gateway_fields() -> None:
    meta = InvocationMetadata(
        task_type="external_llm_call",
        model_alias="gateway/test",
        model_actual="ollama/qwen",
        input_hash="abc123",
        latency_ms=500,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        user_id="gateway",
        queue_wait_ms=1200,
        interrupted=True,
        chain_id="chain-xyz",
        caller="immich-tagger",
    )
    assert meta.queue_wait_ms == 1200
    assert meta.interrupted is True
    assert meta.chain_id == "chain-xyz"
    assert meta.caller == "immich-tagger"


class TestLLMHealth:
    async def test_health_ok(self) -> None:
        ollama = AsyncMock()
        ollama.health = AsyncMock(return_value=True)
        request = _make_request(ollama=ollama)
        result = await llm_health(request)
        assert result["ok"] is True

    async def test_health_no_provider(self) -> None:
        request = _make_request(ollama=None)
        result = await llm_health(request)
        assert result["ok"] is False


class TestLLMModels:
    async def test_list_models(self) -> None:
        ollama = AsyncMock()
        ollama.list_models = AsyncMock(return_value=["model-a", "model-b"])
        request = _make_request(ollama=ollama)
        result = await llm_models(request)
        assert result["models"] == ["model-a", "model-b"]


class TestQueueStatus:
    async def test_returns_status(self) -> None:
        queue = MagicMock()
        queue.get_status.return_value = {
            "current_request": None,
            "internal_queue": {"pending": 0},
            "external_queue": {"pending": 0},
            "stats_24h": {},
            "rate_limits": {},
            "mode": "active",
        }
        request = _make_request(queue=queue)
        result = await llm_queue_status(request)
        assert result["mode"] == "active"
