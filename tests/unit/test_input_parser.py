"""Tests for the input parsing pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.models.router import ModelRouter
from donna.models.types import CompletionMetadata
from donna.models.validation import ValidationError
from donna.orchestrator.input_parser import InputParser, _render_template

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _make_metadata(**overrides: object) -> CompletionMetadata:
    defaults = dict(
        latency_ms=200,
        tokens_in=120,
        tokens_out=80,
        cost_usd=0.0016,
        model_actual="anthropic/claude-sonnet-4-20250514",
    )
    defaults.update(overrides)
    return CompletionMetadata(**defaults)


def _buy_milk_response() -> dict:
    return {
        "title": "Buy milk",
        "description": None,
        "domain": "personal",
        "priority": 1,
        "deadline": None,
        "deadline_type": "none",
        "estimated_duration": 15,
        "recurrence": None,
        "tags": ["shopping"],
        "prep_work_flag": False,
        "agent_eligible": False,
        "confidence": 0.95,
    }


def _pay_bill_response() -> dict:
    return {
        "title": "Pay electric bill",
        "description": None,
        "domain": "personal",
        "priority": 3,
        "deadline": "2026-03-21T17:00:00",
        "deadline_type": "hard",
        "estimated_duration": 10,
        "recurrence": None,
        "tags": ["bills", "finance"],
        "prep_work_flag": False,
        "agent_eligible": False,
        "confidence": 0.90,
    }


@pytest.fixture
def models_config() -> ModelsConfig:
    return ModelsConfig(
        models={
            "parser": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
            "reasoner": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
        },
        routing={
            "parse_task": RoutingEntry(model="parser", fallback="reasoner", confidence_threshold=0.7),
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


@pytest.fixture
def mock_logger() -> AsyncMock:
    mock = AsyncMock()
    mock.log = AsyncMock(return_value="inv-123")
    return mock


class TestRenderTemplate:
    def test_fills_user_input(self) -> None:
        template = "Input: {{ user_input }}"
        result = _render_template(template, "Buy milk")
        assert "Buy milk" in result

    def test_fills_date_and_time(self) -> None:
        template = "Date: {{ current_date }} Time: {{ current_time }}"
        result = _render_template(template, "test")
        # Should contain a date pattern like 2026-03-19
        assert "202" in result
        assert "UTC" in result or ":" in result


class TestInputParser:
    async def test_parse_buy_milk(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        result = await parser.parse("Buy milk", user_id="nick")

        assert result.title == "Buy milk"
        assert result.domain == "personal"
        assert result.priority == 1
        assert result.confidence == 0.95

    async def test_parse_pay_bill_with_deadline(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_pay_bill_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        result = await parser.parse("Pay electric bill by Friday", user_id="nick")

        assert result.title == "Pay electric bill"
        assert result.deadline_type == "hard"
        assert result.deadline is not None

    async def test_invocation_logged(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        await parser.parse("Buy milk", user_id="nick")

        mock_logger.log.assert_called_once()
        call_args = mock_logger.log.call_args[0][0]
        assert call_args.task_type == "parse_task"
        assert call_args.model_alias == "parser"
        assert call_args.cost_usd == 0.0016
        assert call_args.user_id == "nick"

    async def test_malformed_response_raises_validation_error(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        bad_response = {"title": "Buy milk"}  # Missing required fields
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(bad_response, _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)

        with pytest.raises(ValidationError):
            await parser.parse("Buy milk", user_id="nick")

    async def test_prompt_template_rendered(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        await parser.parse("Buy milk", user_id="nick")

        # Verify the prompt was filled with the user input
        called_prompt = router.complete.call_args[0][0]
        assert "Buy milk" in called_prompt
        # Should not still have template variables
        assert "{{ user_input }}" not in called_prompt
        assert "{{ current_date }}" not in called_prompt
