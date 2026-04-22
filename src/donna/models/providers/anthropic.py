"""Anthropic Claude API provider.

Wraps the anthropic Python SDK for async structured completions.
See docs/model-layer.md for the model interface specification.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic
import structlog

from donna.models.providers._parsing import parse_json_response
from donna.models.types import CompletionMetadata

logger = structlog.get_logger()

# Claude Sonnet pricing (per million tokens) as of 2025-05.
_SONNET_INPUT_COST_PER_MTOK = 3.0
_SONNET_OUTPUT_COST_PER_MTOK = 15.0


def _compute_cost(tokens_in: int, tokens_out: int) -> float:
    """Compute USD cost from token counts using Sonnet pricing."""
    return (
        tokens_in * _SONNET_INPUT_COST_PER_MTOK / 1_000_000
        + tokens_out * _SONNET_OUTPUT_COST_PER_MTOK / 1_000_000
    )


class AnthropicProvider:
    """Async Claude API provider.

    Sends prompts to the Anthropic messages API and returns parsed JSON
    with completion metadata. Does not handle retries — the caller
    (ModelRouter) wraps calls with resilient_call().
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Send a prompt and return parsed JSON output with metadata.

        Args:
            prompt: The fully-rendered prompt text.
            model: Anthropic model ID (e.g. "claude-sonnet-4-20250514").
            max_tokens: Maximum output tokens.
            num_ctx: Accepted for Protocol uniformity; ignored by Anthropic.

        Returns:
            Tuple of (parsed JSON dict, CompletionMetadata).

        Raises:
            json.JSONDecodeError: If the response is not valid JSON.
            anthropic.APIError: On API-level failures.
        """
        # num_ctx is Ollama-only; accepted for Protocol uniformity.
        start = time.monotonic()

        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        raw_text = response.content[0].text
        parsed = parse_json_response(raw_text)

        metadata = CompletionMetadata(
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_compute_cost(tokens_in, tokens_out),
            model_actual=f"anthropic/{response.model}",
        )

        logger.info(
            "anthropic_completion",
            model=response.model,
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=metadata.cost_usd,
        )

        return parsed, metadata
