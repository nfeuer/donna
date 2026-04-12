"""Test that OllamaProvider respects json_mode parameter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.models.providers.ollama import OllamaProvider


class TestOllamaJsonMode:
    async def test_json_mode_true_includes_format(self) -> None:
        provider = OllamaProvider(base_url="http://fake:11434")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "message": {"content": '{"result": "ok"}'},
            "model": "test",
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(provider, "_get_session", return_value=mock_session):
            await provider.complete("test prompt", "test-model", json_mode=True)

        payload = mock_session.post.call_args[1]["json"]
        assert payload["format"] == "json"

    async def test_json_mode_false_omits_format(self) -> None:
        provider = OllamaProvider(base_url="http://fake:11434")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "message": {"content": "plain text response"},
            "model": "test",
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(provider, "_get_session", return_value=mock_session):
            await provider.complete("test prompt", "test-model", json_mode=False)

        payload = mock_session.post.call_args[1]["json"]
        assert "format" not in payload
