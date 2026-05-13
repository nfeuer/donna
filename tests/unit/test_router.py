"""Tests for ModelRouter config-driven routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
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
            "parse_task": RoutingEntry(
                model="parser", fallback="reasoner", confidence_threshold=0.7,
            ),
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


class TestOllamaFallbackTracking:
    """Tests for _ollama_degraded flag and fallback/recovery log events."""

    @pytest.fixture
    def ollama_models_config(self) -> ModelsConfig:
        """Config with an Ollama primary model and an Anthropic fallback."""
        return ModelsConfig(
            models={
                "local": ModelConfig(
                    provider="ollama",
                    model="qwen2.5:32b-instruct-q6_K",
                    num_ctx=8192,
                    estimated_cost_per_1k_tokens=0.0001,
                ),
                "cloud_fallback": ModelConfig(
                    provider="anthropic",
                    model="claude-sonnet-4-20250514",
                ),
            },
            routing={
                "summarize": RoutingEntry(
                    model="local",
                    fallback="cloud_fallback",
                ),
            },
            ollama=OllamaConfig(
                base_url="http://localhost:11434",
                timeout_s=30,
                default_num_ctx=8192,
                default_output_reserve=1024,
            ),
        )

    @pytest.fixture
    def ollama_task_types_config(self) -> TaskTypesConfig:
        return TaskTypesConfig(
            task_types={
                "summarize": TaskTypeEntry(
                    description="Summarize content",
                    model="local",
                    prompt_template="prompts/parse_task.md",
                    output_schema="schemas/task_parse_output.json",
                ),
            }
        )

    @pytest.fixture
    def ollama_router(
        self,
        ollama_models_config: ModelsConfig,
        ollama_task_types_config: TaskTypesConfig,
    ) -> ModelRouter:
        """ModelRouter with a mocked Ollama provider to avoid aiohttp dependency."""
        mock_ollama_provider = MagicMock()
        mock_ollama_provider.complete = AsyncMock()

        # Patch the provider registry so "ollama" resolves to our mock class.
        mock_ollama_cls = MagicMock(return_value=mock_ollama_provider)
        with patch(
            "donna.models.router._PROVIDER_REGISTRY",
            {"anthropic": __import__(
                "donna.models.providers.anthropic", fromlist=["AnthropicProvider"]
            ).AnthropicProvider, "ollama": mock_ollama_cls},
        ):
            return ModelRouter(ollama_models_config, ollama_task_types_config, PROJECT_ROOT)

    def test_ollama_degraded_starts_false(
        self, ollama_router: ModelRouter
    ) -> None:
        """_ollama_degraded flag must be False on a freshly constructed router."""
        assert ollama_router._ollama_degraded is False

    async def test_fallback_sets_degraded_flag(
        self, ollama_router: ModelRouter
    ) -> None:
        """Context overflow to cloud fallback must set _ollama_degraded=True."""
        # Build a prompt long enough to overflow the Ollama context budget.
        # default_num_ctx=8192, default_output_reserve=1024 → budget=7168 tokens.
        # Each word ≈ 1 token; 8000 words safely exceeds the budget.
        large_prompt = " ".join(["word"] * 8000)

        cloud_response = (
            {"title": "summary", "domain": "test", "priority": 1},
            CompletionMetadata(
                latency_ms=100,
                tokens_in=50,
                tokens_out=20,
                cost_usd=0.001,
                model_actual="anthropic/claude-sonnet-4-20250514",
            ),
        )

        with patch.object(
            ollama_router._providers["anthropic"],
            "complete",
            new_callable=AsyncMock,
            return_value=cloud_response,
        ):
            await ollama_router.complete(large_prompt, "summarize")

        assert ollama_router._ollama_degraded is True

    async def test_ollama_success_clears_degraded_flag(
        self, ollama_router: ModelRouter
    ) -> None:
        """A successful Ollama call after a fallback must clear _ollama_degraded."""
        # Pre-set degraded flag as if a previous overflow occurred.
        ollama_router._ollama_degraded = True

        short_prompt = "Brief summary please."
        ollama_response = (
            {"title": "ok", "domain": "test", "priority": 1},
            CompletionMetadata(
                latency_ms=80,
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.00001,
                model_actual="ollama/qwen2.5:32b-instruct-q6_K",
            ),
        )

        # Patch the ollama provider's complete method directly.
        ollama_provider = ollama_router._providers["ollama"]
        ollama_provider.complete = AsyncMock(return_value=ollama_response)

        await ollama_router.complete(short_prompt, "summarize")

        assert ollama_router._ollama_degraded is False

    async def test_fallback_activation_logged(
        self, ollama_router: ModelRouter, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Context overflow fallback must emit 'ollama_fallback_activated' event."""
        large_prompt = " ".join(["word"] * 8000)
        cloud_response = (
            {"title": "summary", "domain": "test", "priority": 1},
            CompletionMetadata(
                latency_ms=100,
                tokens_in=50,
                tokens_out=20,
                cost_usd=0.001,
                model_actual="anthropic/claude-sonnet-4-20250514",
            ),
        )

        import structlog
        events: list[str] = []

        def capture_event(logger, method, event_dict):  # type: ignore[no-untyped-def]
            events.append(event_dict.get("event", ""))
            raise structlog.DropEvent()

        with (
            structlog.testing.capture_logs() as cap_logs,
            patch.object(
                ollama_router._providers["anthropic"],
                "complete",
                new_callable=AsyncMock,
                return_value=cloud_response,
            ),
        ):
            await ollama_router.complete(large_prompt, "summarize")

        event_names = [e["event"] for e in cap_logs]
        assert "ollama_fallback_activated" in event_names
