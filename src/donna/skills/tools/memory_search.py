"""memory_search — semantic retrieval over the memory store (slice 13).

Returns provenance-tagged :class:`~donna.memory.store.RetrievedChunk`
records as JSON-friendly dicts. Results are already filtered against
``retrieval.min_score``; the caller can apply further filtering via
``filters`` (e.g. ``{"path_prefix": "Projects/"}``).
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class MemorySearchError(Exception):
    """Raised when ``memory_search`` cannot complete the query."""


async def memory_search(
    *,
    store: Any,
    query: str,
    user_id: str,
    k: int | None = None,
    sources: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a semantic search and return top-``k`` chunks as a dict."""
    try:
        hits = await store.search(
            query=query,
            user_id=user_id,
            k=k,
            sources=sources,
            filters=filters,
        )
    except Exception as exc:
        logger.warning("memory_search_failed", query=query, error=str(exc))
        raise MemorySearchError(f"memory_search: {exc}") from exc
    return {
        "ok": True,
        "query": query,
        "count": len(hits),
        "results": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "source_type": h.source_type,
                "source_path": h.source_path,
                "title": h.title,
                "heading_path": h.heading_path,
                "content": h.content,
                "score": round(h.score, 4),
                "sensitive": h.sensitive,
                "metadata": h.metadata,
            }
            for h in hits
        ],
    }
