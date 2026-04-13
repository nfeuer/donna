"""Router-level context-budget checks and cloud escalation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.models.router import ContextOverflowError, ModelRouter
from donna.models.types import CompletionMetadata


class _RecordingProvider:
    """Minimal provider stub that records every call."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        self.calls.append(
            {"prompt": prompt, "model": model, "num_ctx": num_ctx}
        )
        return (
            {"ok": True, "provider": self.name},
            CompletionMetadata(
                latency_ms=1,
                tokens_in=len(prompt) // 4,
                tokens_out=5,
                cost_usd=0.0,
                model_actual=f"{self.name}/{model}",
            ),
        )


def _build_router(
    *,
    num_ctx: int = 100,
    output_reserve: int = 20,
    with_fallback: bool = True,
) -> tuple[ModelRouter, _RecordingProvider, _RecordingProvider]:
    """Build a stubbed ModelRouter by bypassing __init__.

    Keep the private-attribute assignments below in sync with any new
    required attributes added to ModelRouter.__init__.
    """
    ollama = _RecordingProvider("ollama")
    anthropic = _RecordingProvider("anthropic")

    models_config = ModelsConfig(
        models={
            "local_parser": ModelConfig(
                provider="ollama",
                model="qwen2.5:32b-instruct-q6_K",
                num_ctx=num_ctx,
            ),
            "parser": ModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
            ),
        },
        routing={
            "generate_nudge": RoutingEntry(
                model="local_parser",
                fallback="parser" if with_fallback else None,
            ),
        },
        ollama=OllamaConfig(
            default_num_ctx=num_ctx,
            default_output_reserve=output_reserve,
        ),
    )
    task_types_config = TaskTypesConfig(
        task_types={
            "generate_nudge": TaskTypeEntry(
                description="",
                model="local_parser",
                prompt_template="unused.md",
                output_schema="unused.json",
            ),
        }
    )

    router = ModelRouter.__new__(ModelRouter)
    router._models_config = models_config
    router._task_types_config = task_types_config
    router._project_root = Path(".")
    router._budget_guard = None
    router._on_shadow_complete = None
    router._providers = {"ollama": ollama, "anthropic": anthropic}
    router._prompt_cache = {}
    router._schema_cache = {}
    from donna.resilience.retry import CircuitBreaker
    router._circuit_breaker = CircuitBreaker()

    return router, ollama, anthropic


@pytest.mark.asyncio
async def test_small_prompt_dispatches_to_local() -> None:
    router, ollama, anthropic = _build_router()
    # num_ctx=100, reserve=20 → budget=80 tokens → 80*4=320 chars fit
    prompt = "x" * 200  # 200 chars ≈ 50 tokens
    _, meta = await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 1
    assert len(anthropic.calls) == 0
    assert meta.model_actual.startswith("ollama/")


@pytest.mark.asyncio
async def test_large_prompt_escalates_to_fallback() -> None:
    router, ollama, anthropic = _build_router()
    # budget=80 tokens → anything > 80 tokens (>320 chars) overflows
    prompt = "x" * 2000  # 2000 chars ≈ 500 tokens
    _, meta = await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 0
    assert len(anthropic.calls) == 1
    assert meta.model_actual.startswith("anthropic/")


@pytest.mark.asyncio
async def test_large_prompt_no_fallback_raises_context_overflow_error() -> None:
    router, ollama, anthropic = _build_router(with_fallback=False)
    prompt = "x" * 2000
    with pytest.raises(ContextOverflowError):
        await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 0
    assert len(anthropic.calls) == 0


@pytest.mark.asyncio
async def test_local_dispatch_forwards_num_ctx_to_provider() -> None:
    router, ollama, _ = _build_router(num_ctx=4096)
    await router.complete(prompt="small", task_type="generate_nudge")
    assert ollama.calls[0]["num_ctx"] == 4096


@pytest.mark.asyncio
async def test_metadata_carries_estimated_and_overflow_flag() -> None:
    router, _, _ = _build_router()
    _, meta = await router.complete(prompt="x" * 200, task_type="generate_nudge")
    assert meta.estimated_tokens_in == 50  # 200 // 4
    assert meta.overflow_escalated is False


@pytest.mark.asyncio
async def test_metadata_marks_overflow_escalation() -> None:
    router, _, _ = _build_router()
    _, meta = await router.complete(prompt="x" * 2000, task_type="generate_nudge")
    assert meta.estimated_tokens_in == 500  # 2000 // 4
    assert meta.overflow_escalated is True
