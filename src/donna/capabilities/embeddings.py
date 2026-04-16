"""Embedding helper for capability semantic search.

Uses sentence-transformers' all-MiniLM-L6-v2 (384-dim, ~80MB, CPU-friendly).
The model is lazy-loaded on first use to avoid import-time cost.
"""

from __future__ import annotations

import threading

import numpy as np

EMBEDDING_DIM = 384
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_text(text: str) -> np.ndarray:
    model = _get_model()
    vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return vec.astype(np.float32)


def embedding_to_bytes(vec: np.ndarray) -> bytes:
    assert vec.dtype == np.float32
    assert vec.shape == (EMBEDDING_DIM,)
    return vec.tobytes()


def bytes_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
