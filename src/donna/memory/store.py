"""MemoryStore — semantic index over vault + episodic content.

Backed by sqlite-vec inside ``donna_tasks.db``:

- ``memory_documents``: one row per ingested source (vault note, chat
  turn, …). ``(user_id, source_type, source_id)`` is unique. Soft-
  deleted via ``deleted_at`` so ``search`` can filter without having
  to mutate the ANN index on every tombstone.
- ``memory_chunks``: one row per chunk emitted by the chunker,
  carrying the content and the ``heading_path`` provenance stack.
- ``vec_memory_chunks``: the sqlite-vec ``vec0`` virtual table —
  ``chunk_id`` + float32[dim] embedding.

All three tables are kept consistent by wrapping the upsert path in a
single transaction. On re-upsert of an unchanged document
(``content_hash`` match) we short-circuit before invoking the
embedding provider — the existence of that skip is what makes the
``invocation_log`` row count meaningful as a dedup signal.

Distance → score: sqlite-vec's ``vec0`` reports the L2 distance
between query and stored vectors. For L2-normalised embeddings
(MiniLM outputs are), ``|a-b|^2 = 2 - 2·cos(a,b)``, so the cosine
score is ``1 - distance**2 / 2``. We clamp into ``[0, 1]`` and filter
by ``retrieval.min_score``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiosqlite
import numpy as np
import structlog

from donna.config import VaultRetrievalConfig
from donna.memory.chunking import Chunk, Chunker
from donna.memory.embeddings import EmbeddingProvider

logger = structlog.get_logger()


@dataclass(frozen=True)
class Document:
    """A source document to ingest."""

    user_id: str
    source_type: str
    source_id: str
    title: str | None
    uri: str | None
    content: str
    sensitive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    """A search hit — chunk content plus provenance."""

    chunk_id: str
    document_id: str
    content: str
    heading_path: list[str]
    score: float
    source_type: str
    source_id: str
    source_path: str
    title: str | None
    sensitive: bool
    metadata: dict[str, Any]


def _hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vec_to_blob(vec: np.ndarray) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1:
        raise ValueError(f"embedding must be 1-D, got shape {arr.shape}")
    return arr.tobytes()


def _distance_to_score(distance: float) -> float:
    """Convert vec0 L2 distance (unit vectors) to cosine similarity."""
    cos = 1.0 - (distance * distance) / 2.0
    if cos < 0.0:
        return 0.0
    if cos > 1.0:
        return 1.0
    return float(cos)


class MemoryStore:
    """Persistent semantic memory. Async-safe around a single aiosqlite conn."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        provider: EmbeddingProvider,
        chunker: Chunker,
        retrieval_cfg: VaultRetrievalConfig,
        *,
        query_task_type: str = "embed_memory_query",
    ) -> None:
        self._conn = conn
        self._provider = provider
        self._chunker = chunker
        self._retrieval_cfg = retrieval_cfg
        self._query_task_type = query_task_type

    # -- writes -------------------------------------------------------

    async def put(self, doc: Document) -> str:
        """Insert a new document (fails if `(user_id, source_type, source_id)` exists)."""
        return await self._upsert(doc, allow_existing=False)

    async def upsert(self, doc: Document) -> str:
        """Insert or update a document.

        Short-circuits re-embedding when the new ``content_hash`` matches
        the stored one — only ``updated_at`` / ``deleted_at`` / sensitive
        / title / metadata are refreshed.
        """
        return await self._upsert(doc, allow_existing=True)

    async def upsert_many(self, docs: list[Document]) -> list[str]:
        """Batched variant. One `embed_batch` call per flush."""
        if not docs:
            return []
        # Short-circuit each doc whose hash matches first so the batch
        # sent to the embedding provider is as small as possible.
        hashes = [_hash_content(d.content) for d in docs]
        existing = await self._fetch_existing_many(docs)
        to_embed_chunks: list[str] = []
        plan: list[tuple[Document, str, str | None, str, list[Chunk] | None]] = []
        for doc, content_hash in zip(docs, hashes, strict=True):
            prev_id, prev_hash = existing.get(
                (doc.user_id, doc.source_type, doc.source_id), (None, None)
            )
            if prev_id is not None and prev_hash == content_hash:
                plan.append((doc, content_hash, prev_id, "touch", None))
                continue
            chunks = self._chunker.chunk(doc.content)
            to_embed_chunks.extend(c.content for c in chunks)
            plan.append((doc, content_hash, prev_id, "reindex", chunks))
        vectors: list[np.ndarray] = []
        if to_embed_chunks:
            vectors = await self._provider.embed_batch(to_embed_chunks)

        out_ids: list[str] = []
        vec_cursor = 0
        now = datetime.utcnow()
        await self._conn.execute("BEGIN")
        try:
            for doc, content_hash, prev_id, mode, chunks in plan:
                if mode == "touch":
                    assert prev_id is not None
                    await self._touch_document(prev_id, doc, now)
                    out_ids.append(prev_id)
                    continue
                assert chunks is not None
                chunk_vectors = vectors[vec_cursor : vec_cursor + len(chunks)]
                vec_cursor += len(chunks)
                doc_id = prev_id or str(uuid.uuid4())
                await self._write_document(
                    doc_id, doc, content_hash, now, is_insert=prev_id is None
                )
                await self._replace_chunks(doc_id, doc, chunks, chunk_vectors, now)
                out_ids.append(doc_id)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return out_ids

    async def _upsert(self, doc: Document, *, allow_existing: bool) -> str:
        content_hash = _hash_content(doc.content)
        row = await self._fetch_existing(doc)
        now = datetime.utcnow()
        if row is not None and not allow_existing:
            raise ValueError(
                f"Document already exists: user={doc.user_id} "
                f"source={doc.source_type}:{doc.source_id}"
            )
        if row is not None and row[1] == content_hash:
            await self._touch_document(row[0], doc, now)
            return row[0]

        chunks = self._chunker.chunk(doc.content)
        vectors: list[np.ndarray] = []
        if chunks:
            vectors = await self._provider.embed_batch([c.content for c in chunks])

        doc_id = row[0] if row is not None else str(uuid.uuid4())
        await self._conn.execute("BEGIN")
        try:
            await self._write_document(
                doc_id, doc, content_hash, now, is_insert=row is None
            )
            await self._replace_chunks(doc_id, doc, chunks, vectors, now)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return doc_id

    async def _fetch_existing(self, doc: Document) -> tuple[str, str] | None:
        async with self._conn.execute(
            "SELECT id, content_hash FROM memory_documents "
            "WHERE user_id=? AND source_type=? AND source_id=?",
            (doc.user_id, doc.source_type, doc.source_id),
        ) as cur:
            return await cur.fetchone()

    async def _fetch_existing_many(
        self, docs: list[Document]
    ) -> dict[tuple[str, str, str], tuple[str, str]]:
        out: dict[tuple[str, str, str], tuple[str, str]] = {}
        for doc in docs:
            row = await self._fetch_existing(doc)
            if row is not None:
                out[(doc.user_id, doc.source_type, doc.source_id)] = row
        return out

    async def _touch_document(
        self, doc_id: str, doc: Document, now: datetime
    ) -> None:
        await self._conn.execute(
            "UPDATE memory_documents "
            "SET updated_at=?, deleted_at=NULL, sensitive=?, title=?, "
            "    metadata_json=?, uri=? "
            "WHERE id=?",
            (
                now,
                int(bool(doc.sensitive)),
                doc.title,
                json.dumps(doc.metadata) if doc.metadata else None,
                doc.uri,
                doc_id,
            ),
        )
        await self._conn.commit()

    async def _write_document(
        self,
        doc_id: str,
        doc: Document,
        content_hash: str,
        now: datetime,
        *,
        is_insert: bool,
    ) -> None:
        if is_insert:
            await self._conn.execute(
                "INSERT INTO memory_documents "
                "(id, user_id, source_type, source_id, title, uri, "
                " content_hash, created_at, updated_at, deleted_at, "
                " sensitive, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (
                    doc_id,
                    doc.user_id,
                    doc.source_type,
                    doc.source_id,
                    doc.title,
                    doc.uri,
                    content_hash,
                    now,
                    now,
                    int(bool(doc.sensitive)),
                    json.dumps(doc.metadata) if doc.metadata else None,
                ),
            )
        else:
            await self._conn.execute(
                "UPDATE memory_documents "
                "SET title=?, uri=?, content_hash=?, updated_at=?, "
                "    deleted_at=NULL, sensitive=?, metadata_json=? "
                "WHERE id=?",
                (
                    doc.title,
                    doc.uri,
                    content_hash,
                    now,
                    int(bool(doc.sensitive)),
                    json.dumps(doc.metadata) if doc.metadata else None,
                    doc_id,
                ),
            )

    async def _replace_chunks(
        self,
        doc_id: str,
        doc: Document,
        chunks: list[Chunk],
        vectors: list[np.ndarray],
        now: datetime,
    ) -> None:
        # Drop any chunks from a prior version of this document. Both
        # tables need to stay aligned — prior chunk_ids in vec must go
        # too. We read them first so we can DELETE by primary key.
        async with self._conn.execute(
            "SELECT chunk_id FROM memory_chunks WHERE document_id=?",
            (doc_id,),
        ) as cur:
            old_rows = await cur.fetchall()
        for (old_chunk_id,) in old_rows:
            await self._conn.execute(
                "DELETE FROM vec_memory_chunks WHERE chunk_id=?",
                (old_chunk_id,),
            )
        await self._conn.execute(
            "DELETE FROM memory_chunks WHERE document_id=?",
            (doc_id,),
        )

        version_tag = self._provider.version_tag
        for chunk, vec in zip(chunks, vectors, strict=True):
            chunk_id = str(uuid.uuid4())
            await self._conn.execute(
                "INSERT INTO memory_chunks "
                "(chunk_id, document_id, user_id, chunk_index, content, "
                " content_hash, heading_path, token_count, "
                " embedding_version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    chunk_id,
                    doc_id,
                    doc.user_id,
                    chunk.index,
                    chunk.content,
                    _hash_content(chunk.content),
                    json.dumps(chunk.heading_path),
                    chunk.token_count,
                    version_tag,
                    now,
                ),
            )
            await self._conn.execute(
                "INSERT INTO vec_memory_chunks (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, _vec_to_blob(vec)),
            )

    async def delete(
        self, *, source_type: str, source_id: str, user_id: str
    ) -> bool:
        """Soft-delete a document. Returns True if any row was affected."""
        now = datetime.utcnow()
        cur = await self._conn.execute(
            "UPDATE memory_documents "
            "SET deleted_at=?, updated_at=? "
            "WHERE user_id=? AND source_type=? AND source_id=? "
            "  AND deleted_at IS NULL",
            (now, now, user_id, source_type, source_id),
        )
        await self._conn.commit()
        changed = cur.rowcount > 0
        if changed:
            logger.info(
                "memory_document_deleted",
                source_type=source_type,
                source_id=source_id,
                user_id=user_id,
            )
        return changed

    async def reindex(
        self, *, user_id: str, source_type: str | None = None
    ) -> int:
        """Force re-embedding of matching documents. Returns count."""
        where = ["user_id=?", "deleted_at IS NULL"]
        params: list[Any] = [user_id]
        if source_type is not None:
            where.append("source_type=?")
            params.append(source_type)
        async with self._conn.execute(
            "SELECT id, source_type, source_id, title, uri, "
            "       sensitive, metadata_json "
            "FROM memory_documents WHERE " + " AND ".join(where),
            params,
        ) as cur:
            rows = await cur.fetchall()
        count = 0
        for (_id, stype, sid, title, uri, sensitive, metadata_json) in rows:
            async with self._conn.execute(
                "SELECT content FROM memory_chunks WHERE document_id=? "
                "ORDER BY chunk_index",
                (_id,),
            ) as ccur:
                chunk_rows = await ccur.fetchall()
            content = "\n\n".join(c[0] for c in chunk_rows)
            doc = Document(
                user_id=user_id,
                source_type=stype,
                source_id=sid,
                title=title,
                uri=uri,
                content=content,
                sensitive=bool(sensitive),
                metadata=json.loads(metadata_json) if metadata_json else {},
            )
            # Bust the hash cache by prepending a reindex marker — the
            # upsert will still observe the caller's real content via
            # chunks, but the document row's hash must differ for the
            # short-circuit to miss.
            await self._conn.execute(
                "UPDATE memory_documents SET content_hash='' WHERE id=?",
                (_id,),
            )
            await self._conn.commit()
            await self.upsert(dataclasses.replace(doc, content=content))
            count += 1
        return count

    # -- reads --------------------------------------------------------

    async def search(
        self,
        *,
        query: str,
        user_id: str,
        k: int | None = None,
        sources: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        """Embed ``query`` and return the top-``k`` hits."""
        k_eff = min(
            k if k is not None else self._retrieval_cfg.default_k,
            self._retrieval_cfg.max_k,
        )
        # Pull a wider vec0 window so post-filters (user_id, sources,
        # soft-delete) still leave us with k_eff rows in most cases.
        window = max(k_eff * 4, 32)
        t0 = time.monotonic()
        vec = await self._provider.embed(query)
        blob = _vec_to_blob(vec)

        sql = (
            "SELECT c.chunk_id, c.document_id, c.content, c.heading_path, "
            "       c.chunk_index, v.distance, d.source_type, d.source_id, "
            "       d.title, d.sensitive, d.metadata_json, d.uri "
            "FROM vec_memory_chunks v "
            "JOIN memory_chunks c ON c.chunk_id = v.chunk_id "
            "JOIN memory_documents d ON d.id = c.document_id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "  AND d.user_id = ? AND d.deleted_at IS NULL"
        )
        params: list[Any] = [blob, window, user_id]
        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql += f" AND d.source_type IN ({placeholders})"
            params.extend(sources)
        sql += " ORDER BY v.distance"

        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

        hits: list[RetrievedChunk] = []
        path_prefix = (filters or {}).get("path_prefix")
        for (
            chunk_id,
            document_id,
            content,
            heading_path_json,
            _chunk_index,
            distance,
            source_type,
            source_id,
            title,
            sensitive,
            metadata_json,
            _uri,
        ) in rows:
            score = _distance_to_score(float(distance))
            if score < self._retrieval_cfg.min_score:
                continue
            if path_prefix and not source_id.startswith(path_prefix):
                continue
            heading_path: list[str] = (
                json.loads(heading_path_json) if heading_path_json else []
            )
            metadata: dict[str, Any] = (
                json.loads(metadata_json) if metadata_json else {}
            )
            metadata = dict(metadata)
            metadata["sensitive"] = bool(sensitive)
            hits.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    content=content,
                    heading_path=[h for h in heading_path if h],
                    score=score,
                    source_type=source_type,
                    source_id=source_id,
                    source_path=source_id,
                    title=title,
                    sensitive=bool(sensitive),
                    metadata=metadata,
                )
            )
            if len(hits) >= k_eff:
                break

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "memory_retrieval",
            query_len=len(query),
            hits=len(hits),
            k=k_eff,
            latency_ms=latency_ms,
            sources=sources or ["*"],
            user_id=user_id,
        )
        return hits

    async def get_document_meta(
        self, *, source_type: str, source_id: str, user_id: str
    ) -> tuple[str, datetime] | None:
        """Return (document_id, updated_at) for mtime-based short-circuit."""
        async with self._conn.execute(
            "SELECT id, updated_at FROM memory_documents "
            "WHERE user_id=? AND source_type=? AND source_id=? "
            "  AND deleted_at IS NULL",
            (user_id, source_type, source_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        doc_id, updated = row
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        return doc_id, updated


__all__ = [
    "Document",
    "MemoryStore",
    "RetrievedChunk",
]
