"""Unit tests for :mod:`donna.memory.embeddings`."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.config import VaultEmbeddingConfig
from donna.memory.embeddings import (
    EmbeddingProvider,
    MiniLMProvider,
    build_embedding_provider,
)


def test_fake_provider_satisfies_protocol(fake_provider) -> None:
    assert isinstance(fake_provider, EmbeddingProvider)


def test_minilm_provider_satisfies_protocol() -> None:
    p = MiniLMProvider(version_tag="v1", max_tokens=256)
    assert isinstance(p, EmbeddingProvider)
    assert p.name == "minilm-l6-v2"
    assert p.dim == 384


def test_build_embedding_provider_minilm() -> None:
    cfg = VaultEmbeddingConfig(provider="minilm-l6-v2", version_tag="t", max_tokens=256)
    p = build_embedding_provider(cfg, invocation_logger=None, user_id="nick")
    assert isinstance(p, MiniLMProvider)
    assert p.version_tag == "t"
    assert p.max_tokens == 256


def test_build_embedding_provider_unknown_raises() -> None:
    cfg = VaultEmbeddingConfig(provider="does-not-exist")
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedding_provider(cfg, invocation_logger=None, user_id="nick")


@pytest.mark.asyncio
async def test_fake_provider_batch_matches_single(fake_provider) -> None:
    v1 = await fake_provider.embed("hello")
    batch = await fake_provider.embed_batch(["hello"])
    assert batch[0].tolist() == v1.tolist()


@pytest.mark.asyncio
async def test_minilm_provider_logs_invocation() -> None:
    # Stub out the real model; we only care that _log_one fires.
    logger = AsyncMock()
    provider = MiniLMProvider(
        version_tag="t",
        max_tokens=256,
        invocation_logger=logger,
        user_id="nick",
        task_type="embed_memory_query",
    )

    import numpy as np

    from donna.capabilities import embeddings as cap_embed

    fake_vec = np.zeros(384, dtype=np.float32)

    def _fake_encode(_text: str) -> np.ndarray:
        return fake_vec

    orig = cap_embed.embed_text
    cap_embed.embed_text = _fake_encode  # type: ignore[assignment]
    try:
        await provider.embed("hello world")
    finally:
        cap_embed.embed_text = orig  # type: ignore[assignment]

    logger.log.assert_awaited_once()
    (arg,) = logger.log.await_args.args
    assert arg.task_type == "embed_memory_query"
    assert arg.model_alias == "minilm-l6-v2"
    assert arg.tokens_in == 0
    assert arg.cost_usd == 0.0
