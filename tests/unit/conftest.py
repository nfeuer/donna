"""Unit-test-only fixtures.

Tests under ``tests/unit`` must run offline. This conftest stubs the
sentence-transformers model used by
``donna.capabilities.embeddings`` so unit tests that register
capabilities don't attempt a HuggingFace download.

Tests that genuinely need the real embedding model (semantic ranking,
similarity thresholds) must be marked ``@pytest.mark.slow`` — those are
excluded from the default CI run and, when run, get the real model.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest


class _StubSentenceTransformer:
    """Deterministic drop-in for sentence_transformers.SentenceTransformer.

    Produces a reproducible 384-d float32 vector from a SHA-256 hash of the
    input text. Enough for CRUD-level tests that only need *some* embedding
    stored in the DB; not meaningful for semantic-ranking tests.
    """

    def encode(
        self,
        text: str,
        *,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = (digest * (384 // len(digest) + 1))[:384]
        vec = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        return vec / 255.0


@pytest.fixture(autouse=True)
def _stub_capability_embeddings(request, monkeypatch):
    """Replace the capability-embedding model with an offline stub.

    Skipped for tests marked ``slow`` so semantic-ranking tests still get
    the real model when explicitly run.
    """
    if "slow" in request.keywords:
        return

    from donna.capabilities import embeddings

    monkeypatch.setattr(embeddings, "_get_model", lambda: _StubSentenceTransformer())
    monkeypatch.setattr(embeddings, "_model", None, raising=False)
