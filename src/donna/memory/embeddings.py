"""EmbeddingProvider abstraction for the memory layer.

Every embedding goes through a provider so the memory store can stay
model-agnostic. The slice-13 default is :class:`MiniLMProvider`, which
wraps :mod:`donna.capabilities.embeddings` (the single MiniLM-L6-v2
loader shared across the codebase). Swapping providers is a config-only
change; the Protocol is what ``MemoryStore`` depends on.

Every ``embed`` / ``embed_batch`` call writes one ``invocation_log`` row
per input text (CLAUDE.md principle 3). Embeddings are local and free,
so ``tokens_in/out`` and ``cost_usd`` are always zero; the log row is
there for observability + dashboards.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Protocol, runtime_checkable

import numpy as np
import structlog

from donna.capabilities import embeddings as cap_embed
from donna.config import VaultEmbeddingConfig
from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata

logger = structlog.get_logger()


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol every embedding backend must satisfy."""

    name: str
    version_tag: str
    dim: int
    max_tokens: int

    async def embed(self, text: str) -> np.ndarray:
        """Embed a single text. Returns a float32 ``(dim,)`` vector."""
        ...

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None
    ) -> list[np.ndarray]:
        """Embed a list of texts. Returns one vector per input.

        ``task_type`` optionally overrides the provider's default
        invocation-log ``task_type`` for this batch (used by slice
        14 so chat / task / correction embeds are logged distinctly
        from vault embeds). Providers that don't support per-call
        overrides may ignore the kwarg.
        """
        ...


class MiniLMProvider:
    """sentence-transformers/all-MiniLM-L6-v2 via the shared capability module.

    Reuses :func:`donna.capabilities.embeddings.embed_text` (and the
    model cache it manages) — no duplicate load logic. Runs the encode
    on a worker thread via :func:`asyncio.to_thread` so the event loop
    stays free.
    """

    name = "minilm-l6-v2"
    model_actual = cap_embed._MODEL_NAME

    def __init__(
        self,
        version_tag: str,
        *,
        max_tokens: int = 256,
        invocation_logger: InvocationLogger | None = None,
        user_id: str = "nick",
        task_type: str = "embed_vault_chunk",
    ) -> None:
        self.version_tag = version_tag
        self.max_tokens = max_tokens
        self.dim = cap_embed.EMBEDDING_DIM
        self._logger = invocation_logger
        self._user_id = user_id
        self._task_type = task_type

    async def embed(self, text: str) -> np.ndarray:
        t0 = time.monotonic()
        vec = await asyncio.to_thread(cap_embed.embed_text, text)
        latency_ms = int((time.monotonic() - t0) * 1000)
        await self._log_one(text, latency_ms)
        return vec

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None
    ) -> list[np.ndarray]:
        if not texts:
            return []
        t0 = time.monotonic()
        vecs = await asyncio.to_thread(self._encode_batch_sync, texts)
        total_ms = int((time.monotonic() - t0) * 1000)
        # Amortise batch latency across rows — keeps per-chunk cost
        # comparable on the dashboard.
        per_row_ms = max(total_ms // max(len(texts), 1), 1)
        for text in texts:
            await self._log_one(text, per_row_ms, task_type=task_type)
        return vecs

    def _encode_batch_sync(self, texts: list[str]) -> list[np.ndarray]:
        model = cap_embed._get_model()
        arr = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [v.astype(np.float32) for v in arr]

    async def _log_one(
        self, text: str, latency_ms: int, *, task_type: str | None = None
    ) -> None:
        if self._logger is None:
            return
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            await self._logger.log(
                InvocationMetadata(
                    task_type=task_type or self._task_type,
                    model_alias=self.name,
                    model_actual=self.model_actual,
                    input_hash=h,
                    latency_ms=latency_ms,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    user_id=self._user_id,
                )
            )
        except Exception as exc:
            # Invocation logging is best-effort; a bad write must not
            # take the embed pipeline down.
            logger.warning("embed_invocation_log_failed", reason=str(exc))


def build_embedding_provider(
    cfg: VaultEmbeddingConfig,
    *,
    invocation_logger: InvocationLogger | None,
    user_id: str,
    task_type: str = "embed_vault_chunk",
) -> EmbeddingProvider:
    """Factory for embedding providers.

    Raises:
        ValueError: when ``cfg.provider`` does not map to a known
            implementation.
    """
    if cfg.provider == "minilm-l6-v2":
        return MiniLMProvider(
            version_tag=cfg.version_tag,
            max_tokens=cfg.max_tokens,
            invocation_logger=invocation_logger,
            user_id=user_id,
            task_type=task_type,
        )
    raise ValueError(f"Unknown embedding provider: {cfg.provider!r}")


__all__ = [
    "EmbeddingProvider",
    "MiniLMProvider",
    "build_embedding_provider",
]
