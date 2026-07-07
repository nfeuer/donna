"""Integration tests for local LLM routing, fallback, and recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import ModelConfig, ModelsConfig, RoutingEntry, TaskTypesConfig
from donna.models.router import ModelRouter, RoutingError


def _minimal_models_config(
    *, with_ollama: bool = True, with_fallback: bool = False
) -> ModelsConfig:
    models: dict[str, ModelConfig] = {
        "cloud_main": ModelConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_cost_per_token_usd=3e-6,
            output_cost_per_token_usd=15e-6,
        ),
    }
    routing: dict[str, RoutingEntry] = {
        "cloud_task": RoutingEntry(model="cloud_main"),
    }

    if with_ollama:
        models["local_parser"] = ModelConfig(
            provider="ollama",
            model="qwen2.5:32b-instruct-q6_K",
            num_ctx=8192,
        )
        fb = "cloud_main" if with_fallback else None
        routing["local_task"] = RoutingEntry(model="local_parser", fallback=fb)

    return ModelsConfig(models=models, routing=routing)


def _minimal_task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(task_types={})


class TestRouteResolution:
    def test_local_task_resolves_to_ollama(self) -> None:
        cfg = _minimal_models_config()
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        _provider, model_id, alias = router._resolve_route("local_task")
        assert alias == "local_parser"
        assert model_id == "qwen2.5:32b-instruct-q6_K"

    def test_cloud_task_resolves_to_anthropic(self) -> None:
        cfg = _minimal_models_config()
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        _provider, _model_id, alias = router._resolve_route("cloud_task")
        assert alias == "cloud_main"

    def test_unknown_task_raises_routing_error(self) -> None:
        cfg = _minimal_models_config()
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        with pytest.raises(RoutingError, match="Unknown task type"):
            router._resolve_route("nonexistent_task")


class TestOllamaDegradedFlag:
    def test_starts_not_degraded(self) -> None:
        cfg = _minimal_models_config()
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        assert router._ollama_degraded is False

    def test_degraded_flag_can_be_set(self) -> None:
        cfg = _minimal_models_config()
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        router._ollama_degraded = True
        assert router._ollama_degraded is True


class TestPrefixRouting:
    def test_prefix_match_resolves(self) -> None:
        cfg = _minimal_models_config()
        cfg.routing["skill_step"] = RoutingEntry(model="cloud_main")
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        _, _, alias = router._resolve_route("skill_step::cap::step1")
        assert alias == "cloud_main"

    def test_exact_match_takes_precedence(self) -> None:
        cfg = _minimal_models_config(with_ollama=True)
        cfg.routing["local_task::sub"] = RoutingEntry(model="cloud_main")
        router = ModelRouter(cfg, _minimal_task_types_config(), Path("."))
        _, _, alias = router._resolve_route("local_task::sub")
        assert alias == "cloud_main"
        _, _, alias2 = router._resolve_route("local_task")
        assert alias2 == "local_parser"
