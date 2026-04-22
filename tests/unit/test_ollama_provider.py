"""Tests for OllamaProvider local LLM integration."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.models.providers.ollama import OllamaProvider


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider(
        base_url="http://localhost:11434",
        timeout_s=30,
        estimated_cost_per_1k_tokens=0.0001,
    )


def _mock_ollama_response(
    content: dict,
    model: str = "qwen2.5:32b-instruct-q6_K",
    prompt_eval_count: int = 100,
    eval_count: int = 50,
) -> dict:
    return {
        "model": model,
        "message": {"role": "assistant", "content": json.dumps(content)},
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "done": True,
    }


class TestComplete:
    async def test_successful_completion(self, provider: OllamaProvider) -> None:
        expected_output = {"title": "Buy milk", "domain": "personal", "priority": 1}
        mock_resp = _mock_ollama_response(expected_output)

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value=mock_resp)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False

        provider._session = mock_session

        result, metadata = await provider.complete("test prompt", "qwen2.5:32b-instruct-q6_K")

        assert result["title"] == "Buy milk"
        assert result["domain"] == "personal"
        assert metadata.tokens_in == 100
        assert metadata.tokens_out == 50
        assert metadata.model_actual == "ollama/qwen2.5:32b-instruct-q6_K"
        assert metadata.cost_usd > 0

    async def test_cost_calculation(self, provider: OllamaProvider) -> None:
        mock_resp = _mock_ollama_response(
            {"result": "ok"},
            prompt_eval_count=1000,
            eval_count=500,
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value=mock_resp)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False
        provider._session = mock_session

        _, metadata = await provider.complete("test", "model")

        # 1500 tokens * 0.0001 / 1000 = 0.00015
        assert abs(metadata.cost_usd - 0.00015) < 1e-8

    async def test_missing_token_counts(self, provider: OllamaProvider) -> None:
        """Graceful fallback when token counts are missing."""
        resp_data = {
            "model": "qwen2.5:32b-instruct-q6_K",
            "message": {"role": "assistant", "content": '{"ok": true}'},
            "done": True,
        }

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value=resp_data)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.closed = False
        provider._session = mock_session

        _, metadata = await provider.complete("test", "model")
        assert metadata.tokens_in == 0
        assert metadata.tokens_out == 0
        assert metadata.cost_usd == 0.0


class TestHealth:
    async def test_healthy(self, provider: OllamaProvider) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        provider._session = mock_session

        assert await provider.health() is True

    async def test_unhealthy(self, provider: OllamaProvider) -> None:
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        provider._session = mock_session

        assert await provider.health() is False


class TestListModels:
    async def test_list_models(self, provider: OllamaProvider) -> None:
        tags_response = {
            "models": [
                {"name": "qwen2.5:32b-instruct-q6_K"},
                {"name": "llama3.1:8b-q4"},
            ]
        }

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.closed = False
        provider._session = mock_session

        models = await provider.list_models()
        assert "qwen2.5:32b-instruct-q6_K" in models
        assert len(models) == 2
