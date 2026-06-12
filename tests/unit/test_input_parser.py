"""Tests for the input parsing pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

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
            "parse_task": RoutingEntry(
                model="parser", fallback="reasoner", confidence_threshold=0.7,
            ),
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

    def test_fills_personal_context(self) -> None:
        template = "Context:\n{{ personal_context }}\nEnd"
        result = _render_template(template, "test", personal_context="Knows: Alice (coworker)")
        assert "Alice (coworker)" in result
        assert "{{ personal_context }}" not in result

    def test_personal_context_defaults_to_none_marker(self) -> None:
        template = "Context: {{ personal_context }}"
        result = _render_template(template, "test")
        assert "{{ personal_context }}" not in result
        assert "(none)" in result


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

    async def test_invocation_logged_by_router(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        """Invocation logging is handled by ModelRouter.complete(), not InputParser."""
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        await parser.parse("Buy milk", user_id="nick")

        # InputParser no longer logs directly — the router does.
        mock_logger.log.assert_not_called()

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


    async def test_personal_context_injected_into_prompt(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(return_value=(_buy_milk_response(), _make_metadata()))
        parser = InputParser(router, mock_logger, PROJECT_ROOT)

        hit = type("H", (), {"title": "Alice", "content": "Coworker"})()

        class _Store:
            search = AsyncMock(return_value=[hit])

        parser.set_memory_store(_Store())
        await parser.parse("email Alice", user_id="nick")

        called_prompt = router.complete.call_args[0][0]
        assert "Alice" in called_prompt
        assert "- Alice: Coworker" in called_prompt

    async def test_low_confidence_escalates_to_cloud(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        low = _buy_milk_response() | {"confidence": 0.4, "domain": "personal"}
        high = _buy_milk_response() | {"confidence": 0.95, "domain": "work"}
        router.complete = AsyncMock(  # type: ignore[method-assign]
            side_effect=[(low, _make_metadata()), (high, _make_metadata())]
        )

        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        result = await parser.parse("email the client", user_id="nick")

        assert router.complete.await_count == 2
        assert router.complete.await_args_list[1].kwargs["task_type"] == "parse_task_cloud"
        assert result.domain == "work"  # cloud result wins
        assert result.confidence == 0.95

    async def test_high_confidence_does_not_escalate(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        await parser.parse("Buy milk", user_id="nick")
        assert router.complete.await_count == 1

    async def test_cloud_low_confidence_accepted_without_looping(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        low_local = _buy_milk_response() | {"confidence": 0.3, "domain": "personal"}
        low_cloud = _buy_milk_response() | {"confidence": 0.35, "domain": "work"}
        router.complete = AsyncMock(  # type: ignore[method-assign]
            side_effect=[(low_local, _make_metadata()), (low_cloud, _make_metadata())]
        )
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        result = await parser.parse("touch base with someone", user_id="nick")

        # Exactly one escalation — no loop — and the cloud result is accepted as-is.
        assert router.complete.await_count == 2
        assert result.confidence == 0.35
        assert result.domain == "work"

    async def test_preference_applier_overrides_result(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        router.complete = AsyncMock(  # type: ignore[method-assign]
            return_value=(_buy_milk_response(), _make_metadata())
        )

        class _Applier:
            async def apply_for_user(self, result, user_id):
                import dataclasses
                return dataclasses.replace(result, domain="work")

        parser = InputParser(
            router, mock_logger, PROJECT_ROOT, preference_applier=_Applier(),
        )
        result = await parser.parse("Buy milk", user_id="nick")
        assert result.domain == "work"


class TestParsePromptCalibration:
    def test_prompt_contains_duration_anchors(self) -> None:
        text = (PROJECT_ROOT / "prompts" / "parse_task.md").read_text()
        # Quick-comms anchor and the "default low" instruction must be present.
        assert "15" in text
        assert "30" in text
        assert "60" in text
        assert "lower anchor" in text.lower()

    def test_prompt_lists_quick_comm_examples(self) -> None:
        text = (PROJECT_ROOT / "prompts" / "parse_task.md").read_text().lower()
        for example in ("email", "schedule", "touch base"):
            assert example in text
