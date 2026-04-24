"""Batched ingest queue for the memory store.

A plain :class:`asyncio.Queue` feeds a single background worker that
drains up to ``batch_size`` documents (or waits at most ``flush_ms``
after the first arrival), then hands the batch to
:meth:`MemoryStore.upsert_many` — which invokes the embedding
provider once per flush. This is deliberately not reusing
:mod:`donna.llm.queue`: that module is a token-budgeted LLM dispatcher
with preemption and per-provider buckets, none of which apply to
local embedding work.

Shutdown: the orchestrator cancels the worker task along with every
other entry in ``ctx.tasks`` (``cli.py``'s finaliser). A batch in
flight is not flushed on cancel — next boot's vault backfill mtime-
compares and re-enqueues anything that was dropped, so the gap heals
itself on the next run.
"""

from __future__ import annotations

import asyncio

import structlog

from donna.memory.store import Document, MemoryStore

logger = structlog.get_logger()


class MemoryIngestQueue:
    """Coalesces upserts into batches so embedding runs once per flush."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        batch_size: int = 16,
        flush_ms: int = 500,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._store = store
        self._batch_size = batch_size
        self._flush_seconds = flush_ms / 1000.0
        self._queue: asyncio.Queue[Document] = asyncio.Queue()

    async def enqueue(self, doc: Document) -> None:
        """Hand a document to the worker."""
        await self._queue.put(doc)

    def qsize(self) -> int:
        """Current backlog depth (for observability + tests)."""
        return self._queue.qsize()

    async def run_forever(self) -> None:
        """Drain + flush loop. Run until cancelled."""
        while True:
            try:
                batch = await self._drain_one_batch()
            except asyncio.CancelledError:
                raise
            if not batch:
                continue
            try:
                ids = await self._store.upsert_many(batch)
                logger.info(
                    "memory_ingest_batch",
                    n=len(batch),
                    stored=len(ids),
                )
            except Exception as exc:
                # A single bad batch should not take the worker down —
                # log and continue; the vault backfill will retry next
                # time we see the document's mtime.
                logger.warning(
                    "memory_ingest_batch_failed",
                    n=len(batch),
                    reason=str(exc),
                )

    async def _drain_one_batch(self) -> list[Document]:
        # Block for the first item, then collect additional items for
        # up to `flush_ms` or until the batch is full.
        first = await self._queue.get()
        batch: list[Document] = [first]
        deadline = asyncio.get_running_loop().time() + self._flush_seconds
        while len(batch) < self._batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                doc = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                break
            batch.append(doc)
        return batch


__all__ = ["MemoryIngestQueue"]
