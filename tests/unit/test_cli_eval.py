"""Tests for CLI eval harness model argument parsing."""

from __future__ import annotations

import pytest

from donna.cli import _parse_model_arg


class TestParseModelArg:
    def test_ollama_model(self) -> None:
        provider, model = _parse_model_arg("ollama/qwen2.5:32b-instruct-q6_K")
        assert provider == "ollama"
        assert model == "qwen2.5:32b-instruct-q6_K"

    def test_anthropic_model(self) -> None:
        provider, model = _parse_model_arg("anthropic/claude-sonnet-4-20250514")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_model_with_multiple_slashes(self) -> None:
        provider, model = _parse_model_arg("ollama/org/model:tag")
        assert provider == "ollama"
        assert model == "org/model:tag"

    def test_no_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="provider/model"):
            _parse_model_arg("just-a-model-name")

    def test_empty_model_raises(self) -> None:
        with pytest.raises(ValueError, match="provider/model"):
            _parse_model_arg("ollama/")
        # trailing slash results in empty model_id — partition gives ""
        # but we check for truthy model_id

    def test_slash_only_raises(self) -> None:
        with pytest.raises(ValueError, match="provider/model"):
            _parse_model_arg("/")
