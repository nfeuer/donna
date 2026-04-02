"""Model provider abstraction.

Defines the ModelProvider Protocol that all LLM providers must satisfy.
See docs/model-layer.md for the interface specification.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from donna.models.types import CompletionMetadata


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol for LLM provider implementations.

    Every provider (Anthropic, Ollama, etc.) must expose this interface.
    The ModelRouter dispatches calls through this contract.
    """

    async def complete(
        self, prompt: str, model: str, max_tokens: int = 1024
    ) -> tuple[dict[str, Any], CompletionMetadata]: ...
