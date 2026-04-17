import numpy as np
import pytest

from donna.capabilities.embeddings import (
    embed_text,
    embedding_to_bytes,
    bytes_to_embedding,
    cosine_similarity,
    EMBEDDING_DIM,
)


@pytest.mark.slow
def test_embed_text_returns_expected_shape():
    vec = embed_text("check the price of a product")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (EMBEDDING_DIM,)
    assert vec.dtype == np.float32


@pytest.mark.slow
def test_roundtrip_bytes_conversion():
    vec = embed_text("hello world")
    blob = embedding_to_bytes(vec)
    restored = bytes_to_embedding(blob)
    assert np.allclose(vec, restored)


def test_cosine_similarity_identical_vectors():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(-1.0)
