"""Vault source — keep the memory store in sync with the Obsidian vault.

Two code paths:

- :meth:`VaultSource.watch` — long-running ``watchfiles.awatch`` loop
  with a 500 ms coalesce window. Adds / modifies enqueue an upsert;
  deletes soft-delete the corresponding ``memory_documents`` row.
  Slice 16 adds content-hash rename reconciliation: a ``deleted`` +
  ``added`` pair whose contents hash-equal within
  ``rename_window_seconds`` is treated as a rename (update
  ``source_id`` in ``memory_documents`` without re-embedding).
- :meth:`VaultSource.backfill` — boot-time walk of the vault root
  that enqueues any note whose file mtime is newer than the stored
  ``updated_at`` (or absent from the store). Respects
  ``sources.vault.ignore_globs``; path filtering is done on the rel
  path against those globs *and* also delegates to
  :class:`donna.integrations.vault.VaultClient.list` which applies its
  own `ignore_globs` from ``vault.ignore_globs``.

A note with ``donna: local-only`` (or ``donna_sensitive: true``) in
its frontmatter is marked ``sensitive=True``; that flag propagates to
every chunk's ``RetrievedChunk.metadata``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time as _time
from pathlib import Path
from typing import Any

import structlog

from donna.config import VaultConfig, VaultSourceConfig
from donna.integrations.vault import VaultClient, VaultReadError
from donna.memory.queue import MemoryIngestQueue
from donna.memory.store import Document, MemoryStore, _hash_content

logger = structlog.get_logger()

SOURCE_TYPE = "vault"


class _RenameBuffer:
    """In-memory TTL buffer pairing ``deleted`` → ``added`` as renames.

    Keyed by content-hash. Holds FIFO lists per hash so two files with
    identical content in flight simultaneously can both reconcile
    (pop-oldest semantics). Prune is O(n) over entries — the buffer
    is small by construction (bounded by pending renames within
    ``ttl_seconds``).
    """

    def __init__(self, ttl_seconds: float = 2.0) -> None:
        self._ttl = float(ttl_seconds)
        self._by_hash: dict[str, list[tuple[str, float]]] = {}

    def record_delete(self, rel: str, content_hash: str, now: float) -> None:
        self._by_hash.setdefault(content_hash, []).append((rel, now))

    def match_add(
        self, content_hash: str, now: float
    ) -> str | None:
        """Pop-oldest live entry for ``content_hash``; return its old rel."""
        self.prune(now)
        bucket = self._by_hash.get(content_hash)
        if not bucket:
            return None
        rel, _ts = bucket.pop(0)
        if not bucket:
            del self._by_hash[content_hash]
        return rel

    def discard(self, rel: str, content_hash: str) -> bool:
        """Remove ``(rel, *)`` from the bucket. Used by the TTL flush."""
        bucket = self._by_hash.get(content_hash)
        if not bucket:
            return False
        for i, (r, _ts) in enumerate(bucket):
            if r == rel:
                bucket.pop(i)
                if not bucket:
                    del self._by_hash[content_hash]
                return True
        return False

    def prune(self, now: float) -> None:
        expired: list[str] = []
        for h, bucket in self._by_hash.items():
            bucket[:] = [(r, ts) for (r, ts) in bucket if now - ts < self._ttl]
            if not bucket:
                expired.append(h)
        for h in expired:
            del self._by_hash[h]


class VaultSource:
    """Watches the vault and keeps :class:`MemoryStore` in sync."""

    def __init__(
        self,
        *,
        client: VaultClient,
        store: MemoryStore,
        queue: MemoryIngestQueue,
        cfg: VaultSourceConfig,
        vault_cfg: VaultConfig,
        user_id: str,
    ) -> None:
        self._client = client
        self._store = store
        self._queue = queue
        self._cfg = cfg
        self._vault_cfg = vault_cfg
        self._user_id = user_id
        self._root = Path(vault_cfg.root)
        # Combined ignore list: vault-wide globs + per-source globs.
        self._ignore_globs = list(
            dict.fromkeys([*vault_cfg.ignore_globs, *cfg.ignore_globs])
        )
        self._rename_buffer = _RenameBuffer(
            ttl_seconds=cfg.rename_window_seconds
        )
        # Deferred flush tasks keyed by rel so a re-add before TTL can
        # cancel the pending delete.
        self._pending_delete_tasks: dict[str, asyncio.Task[None]] = {}

    # -- watch --------------------------------------------------------

    async def watch(self) -> None:
        """Stream filesystem changes until cancelled."""
        if not self._cfg.enabled:
            logger.info("vault_source_disabled")
            return
        from watchfiles import Change, awatch  # local import so tests can stub

        if not self._root.exists():
            logger.warning(
                "vault_watch_skipped_missing_root",
                root=str(self._root),
            )
            return
        logger.info("vault_watch_start", root=str(self._root))
        async for changes in awatch(
            str(self._root), step=500, debounce=500, recursive=True
        ):
            for change, raw in changes:
                try:
                    rel = self._to_rel(raw)
                except ValueError:
                    continue
                if not rel.endswith(".md") or self._ignored(rel):
                    continue
                logger.info(
                    "vault_watch_event",
                    change=change.name,
                    path=rel,
                )
                try:
                    if change is Change.added:
                        await self._handle_added(rel)
                    elif change is Change.modified:
                        await self._ingest_path(rel)
                    elif change is Change.deleted:
                        await self._handle_deleted(rel)
                except Exception as exc:
                    logger.warning(
                        "vault_watch_event_failed",
                        path=rel,
                        reason=str(exc),
                    )

    # -- rename reconciliation ----------------------------------------

    async def _handle_deleted(self, rel: str) -> None:
        """Buffer the delete for ``rename_window_seconds``; flush if unmatched."""
        meta = await self._store.get_document_meta_with_hash(
            source_type=SOURCE_TYPE, source_id=rel, user_id=self._user_id
        )
        if meta is None:
            # Store never saw this path — no rename could pair with
            # it, and there is nothing to delete.
            return
        _doc_id, content_hash = meta
        now = _time.monotonic()
        self._rename_buffer.record_delete(rel, content_hash, now)
        logger.info(
            "vault_rename_buffered",
            path=rel,
            content_hash=content_hash,
        )
        task = asyncio.create_task(self._flush_delete_after(rel, content_hash))
        self._pending_delete_tasks[rel] = task

    async def _flush_delete_after(self, rel: str, content_hash: str) -> None:
        try:
            await asyncio.sleep(self._cfg.rename_window_seconds)
        except asyncio.CancelledError:
            return
        # If an add paired with this delete, the buffer entry is gone.
        if self._rename_buffer.discard(rel, content_hash):
            await self._store.delete(
                source_type=SOURCE_TYPE,
                source_id=rel,
                user_id=self._user_id,
            )
            logger.info(
                "vault_rename_flushed_as_delete",
                path=rel,
                content_hash=content_hash,
            )
        self._pending_delete_tasks.pop(rel, None)

    async def _handle_added(self, rel: str) -> None:
        """On add, check the rename buffer before re-ingesting."""
        try:
            note = await self._client.read(rel)
        except VaultReadError as exc:
            logger.warning(
                "vault_ingest_read_failed", path=rel, reason=str(exc)
            )
            return

        content_hash = _hash_content(note.content)
        old_rel = self._rename_buffer.match_add(content_hash, _time.monotonic())
        if old_rel is not None and old_rel != rel:
            # Cancel the pending delete flush for the old path.
            pending = self._pending_delete_tasks.pop(old_rel, None)
            if pending is not None:
                pending.cancel()
            renamed = await self._store.rename(
                source_type=SOURCE_TYPE,
                old_source_id=old_rel,
                new_source_id=rel,
                user_id=self._user_id,
            )
            if renamed:
                logger.info(
                    "vault_rename_matched",
                    old_path=old_rel,
                    new_path=rel,
                    content_hash=content_hash,
                )
                return
            # Fall through to normal ingest when the rename couldn't
            # be applied (e.g. collision on ``new_source_id``).
        await self._ingest_path(rel)

    # -- backfill -----------------------------------------------------

    async def backfill(self, user_id: str | None = None) -> int:
        """Walk the vault root and enqueue anything new or newer-on-disk."""
        if not self._cfg.enabled:
            return 0
        uid = user_id or self._user_id
        if not self._root.exists():
            logger.warning(
                "vault_backfill_skipped_missing_root",
                root=str(self._root),
            )
            return 0
        paths = await self._client.list("", recursive=True)
        n = 0
        for rel in paths:
            if not rel.endswith(".md"):
                continue
            if self._ignored(rel):
                continue
            try:
                mtime, _size = await self._client.stat(rel)
            except Exception as exc:
                logger.warning("vault_backfill_stat_failed", path=rel, reason=str(exc))
                continue
            meta = await self._store.get_document_meta(
                source_type=SOURCE_TYPE, source_id=rel, user_id=uid,
            )
            if meta is not None:
                _doc_id, updated_at = meta
                if updated_at.timestamp() >= mtime:
                    continue
            try:
                await self._ingest_path(rel, user_id=uid)
                n += 1
            except Exception as exc:
                logger.warning(
                    "vault_backfill_ingest_failed", path=rel, reason=str(exc),
                )
        logger.info("vault_backfill_done", count=n, user_id=uid)
        return n

    # -- internals ----------------------------------------------------

    async def _ingest_path(self, rel: str, *, user_id: str | None = None) -> None:
        uid = user_id or self._user_id
        try:
            note = await self._client.read(rel)
        except VaultReadError as exc:
            logger.warning("vault_ingest_read_failed", path=rel, reason=str(exc))
            return
        sensitive = self._is_sensitive(note.frontmatter)
        title = self._pick_title(note.frontmatter, rel)
        fm_public = {
            k: v
            for k, v in note.frontmatter.items()
            if k not in ("donna", "donna_sensitive")
        }
        doc = Document(
            user_id=uid,
            source_type=SOURCE_TYPE,
            source_id=rel,
            title=title,
            uri=f"vault:{rel}",
            content=note.content,
            sensitive=sensitive,
            metadata={
                "mtime": note.mtime,
                "size": note.size,
                "frontmatter": fm_public,
            },
        )
        await self._queue.enqueue(doc)

    @staticmethod
    def _is_sensitive(frontmatter: dict[str, Any]) -> bool:
        raw_donna = frontmatter.get("donna")
        if isinstance(raw_donna, str) and raw_donna.strip().lower() == "local-only":
            return True
        return bool(frontmatter.get("donna_sensitive"))

    @staticmethod
    def _pick_title(frontmatter: dict[str, Any], rel: str) -> str:
        value = frontmatter.get("title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return Path(rel).stem

    def _to_rel(self, raw: str) -> str:
        path = Path(raw)
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            # watchfiles may emit absolute paths from a different root
            # (e.g. when a symlink points outside); drop silently.
            raise
        return rel.as_posix()

    def _ignored(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) for pat in self._ignore_globs)


__all__ = ["SOURCE_TYPE", "VaultSource"]
