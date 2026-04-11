"""Unit tests for the LLM gateway routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.api.routes.llm import (
    ChatMessage,
    ChatRequest,
    CompletionRequest,
    llm_chat,
    llm_completion,
    llm_health,
    llm_models,
)
from donna.models.types import CompletionMetadata


def _make_request(
    *,
    ollama: AsyncMock | None = None,
    gateway_config: dict | None = None,
    models_config: dict | None = None,
) -> MagicMock:
    """Build a mock FastAPI request with app.state for LLM gateway."""
    request = MagicMock()
    request.app.state.ollama = ollama
    request.app.state.llm_gateway_config = gateway_config or {}
    request.app.state.models_config = models_config or {}
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request.app.state.db.connection = conn
    return request


def _make_metadata(**overrides) -> CompletionMetadata:
    defaults = {
        "latency_ms": 500,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "model_actual": "ollama/qwen2.5:32b-instruct-q6_K",
    }
    defaults.update(overrides)
    return CompletionMetadata(**defaults)


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


class TestLLMCompletion:
    async def test_completion_success(self) -> None:
        meta = _make_metadata()
        ollama = AsyncMock()
        ollama.complete = AsyncMock(return_value=({"result": "ok"}, meta))
        request = _make_request(ollama=ollama)

        body = CompletionRequest(prompt="hello", model="test-model")

        with patch("donna.api.routes.llm._log_invocation", new_callable=AsyncMock):
            result = await llm_completion(body, request)

        assert result.output == {"result": "ok"}
        assert result.model == "test-model"
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    async def test_completion_no_provider(self) -> None:
        request = _make_request(ollama=None)
        body = CompletionRequest(prompt="hello")

        with pytest.raises(Exception) as exc_info:
            await llm_completion(body, request)
        assert "503" in str(exc_info.value.status_code)

    async def test_completion_ollama_error(self) -> None:
        ollama = AsyncMock()
        ollama.complete = AsyncMock(side_effect=Exception("timeout"))
        request = _make_request(ollama=ollama)
        body = CompletionRequest(prompt="hello", model="test-model")

        with pytest.raises(Exception) as exc_info:
            await llm_completion(body, request)
        assert "502" in str(exc_info.value.status_code)


class TestLLMChat:
    async def test_chat_success(self) -> None:
        meta = _make_metadata()
        ollama = AsyncMock()
        ollama.complete = AsyncMock(return_value=({"reply": "hi"}, meta))
        request = _make_request(ollama=ollama)

        body = ChatRequest(
            messages=[
                ChatMessage(role="user", content="hello"),
            ],
            model="test-model",
        )

        with patch("donna.api.routes.llm._log_invocation", new_callable=AsyncMock):
            result = await llm_chat(body, request)

        assert result.output == {"reply": "hi"}
        # Verify the prompt was built from messages
        call_args = ollama.complete.call_args
        assert "[user]: hello" in call_args.kwargs["prompt"]


def test_invocation_metadata_has_gateway_fields() -> None:
    from donna.logging.invocation_logger import InvocationMetadata

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
