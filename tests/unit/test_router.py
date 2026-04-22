"""Tests for ModelRouter config-driven routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.models.router import ModelRouter, RoutingError
from donna.models.types import CompletionMetadata

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def models_config() -> ModelsConfig:
    return ModelsConfig(
        models={
            "parser": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
            "reasoner": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
        },
        routing={
            "parse_task": RoutingEntry(model="parser", fallback="reasoner", confidence_threshold=0.7),
            "classify_priority": RoutingEntry(model="parser"),
        },
    )


@pytest.fixture
def task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "parse_task": TaskTypeEntry(
                description="Extract structured task fields",
                model="parser",
                prompt_template="prompts/parse_task.md",
                output_schema="schemas/task_parse_output.json",
            ),
        }
    )


@pytest.fixture
def router(models_config: ModelsConfig, task_types_config: TaskTypesConfig) -> ModelRouter:
    return ModelRouter(models_config, task_types_config, PROJECT_ROOT)


class TestRouteResolution:
    def test_parse_task_resolves_to_parser(self, router: ModelRouter) -> None:
        _provider, model_id, alias = router._resolve_route("parse_task")
        assert alias == "parser"
        assert model_id == "claude-sonnet-4-20250514"

    def test_unknown_task_type_raises(self, router: ModelRouter) -> None:
        with pytest.raises(RoutingError, match="Unknown task type"):
            router._resolve_route("nonexistent_task")

    def test_classify_priority_resolves(self, router: ModelRouter) -> None:
        _provider, _model_id, alias = router._resolve_route("classify_priority")
        assert alias == "parser"


class TestComplete:
    async def test_complete_calls_provider(self, router: ModelRouter) -> None:
        mock_response = (
            {"title": "Buy milk", "domain": "personal", "priority": 1},
            CompletionMetadata(
                latency_ms=150,
                tokens_in=100,
                tokens_out=50,
                cost_usd=0.001,
                model_actual="anthropic/claude-sonnet-4-20250514",
            ),
        )

        with patch.object(
            router._providers["anthropic"],
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result, metadata = await router.complete("test prompt", "parse_task")

        assert result["title"] == "Buy milk"
        assert metadata.latency_ms == 150
        assert metadata.model_actual == "anthropic/claude-sonnet-4-20250514"


class TestPromptAndSchema:
    def test_get_prompt_template(self, router: ModelRouter) -> None:
        template = router.get_prompt_template("parse_task")
        assert "{{ user_input }}" in template
        assert "{{ current_date }}" in template

    def test_get_prompt_template_cached(self, router: ModelRouter) -> None:
        t1 = router.get_prompt_template("parse_task")
        t2 = router.get_prompt_template("parse_task")
        assert t1 is t2

    def test_get_output_schema(self, router: ModelRouter) -> None:
        schema = router.get_output_schema("parse_task")
        assert schema["title"] == "TaskParseOutput"
        assert "title" in schema["properties"]

    def test_unknown_task_type_prompt(self, router: ModelRouter) -> None:
        with pytest.raises(RoutingError):
            router.get_prompt_template("nonexistent")

    def test_unknown_task_type_schema(self, router: ModelRouter) -> None:
        with pytest.raises(RoutingError):
            router.get_output_schema("nonexistent")
