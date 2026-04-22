"""Ollama local LLM provider.

Wraps the Ollama REST API for async structured completions on the
local RTX 3090. See docs/model-layer.md for the model interface
specification.
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp
import structlog

from donna.models.providers._parsing import parse_json_response
from donna.models.types import CompletionMetadata

logger = structlog.get_logger()


class OllamaProvider:
    """Async Ollama API provider for local LLM inference.

    Uses the /api/chat endpoint with streaming disabled.
    Manages a shared aiohttp session (lazy-initialised).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout_s: int = 120,
        estimated_cost_per_1k_tokens: float = 0.0001,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._estimated_cost_per_1k = estimated_cost_per_1k_tokens
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        json_mode: bool = True,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Send a prompt and return parsed output with metadata.

        Args:
            prompt: The fully-rendered prompt text.
            model: Ollama model tag (e.g. "qwen2.5:32b-instruct-q6_K").
            max_tokens: Maximum output tokens.
            json_mode: When True (default), requests JSON format from Ollama
                and parses the response as JSON. When False, returns plain text
                wrapped in {"text": <response>}.
            num_ctx: Context window size to send to Ollama. Defaults to 8192
                when not provided, overriding Ollama's 2048 built-in default.

        Returns:
            Tuple of (parsed dict, CompletionMetadata).

        Raises:
            json.JSONDecodeError: If json_mode=True and the response is not valid JSON.
            aiohttp.ClientError: On connection or HTTP-level failures.
        """
        session = self._get_session()
        start = time.monotonic()

        effective_num_ctx = num_ctx if num_ctx is not None else 8192

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "num_ctx": effective_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"

        async with session.post(
            f"{self._base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        elapsed_ms = int((time.monotonic() - start) * 1000)

        raw_text = data["message"]["content"]
        parsed = parse_json_response(raw_text) if json_mode else {"text": raw_text}

        # Token counts — Ollama provides these at top level.
        # Graceful fallback if fields are missing (older Ollama versions).
        tokens_in = data.get("prompt_eval_count", 0)
        tokens_out = data.get("eval_count", 0)
        total_tokens = tokens_in + tokens_out
        cost = total_tokens * self._estimated_cost_per_1k / 1000

        metadata = CompletionMetadata(
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            model_actual=f"ollama/{data.get('model', model)}",
        )

        logger.info(
            "ollama_completion",
            model=model,
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=metadata.cost_usd,
            num_ctx=effective_num_ctx,
        )

        return parsed, metadata

    async def health(self) -> bool:
        """Check if the Ollama server is reachable.

        Returns True if the /api/tags endpoint responds with HTTP 200.
        """
        try:
            session = self._get_session()
            async with session.get(f"{self._base_url}/api/tags") as resp:
                return resp.status == 200
        except (TimeoutError, aiohttp.ClientError):
            return False

    async def list_models(self) -> list[str]:
        """Return a list of locally available model tags."""
        try:
            session = self._get_session()
            async with session.get(f"{self._base_url}/api/tags") as resp:
                resp.raise_for_status()
                data = await resp.json()
                return [m["name"] for m in data.get("models", [])]
        except (TimeoutError, aiohttp.ClientError):
            return []

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
