"""Vault source — keep the memory store in sync with the Obsidian vault.

Two code paths:

- :meth:`VaultSource.watch` — long-running ``watchfiles.awatch`` loop
  with a 500 ms coalesce window. Adds / modifies enqueue an upsert;
  deletes soft-delete the corresponding ``memory_documents`` row. A
  rename arrives as a delete + add pair; Slice 16 will reconcile those
  into a true rename.
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

import fnmatch
from pathlib import Path
from typing import Any

import structlog

from donna.config import VaultConfig, VaultSourceConfig
from donna.integrations.vault import VaultClient, VaultReadError
from donna.memory.queue import MemoryIngestQueue
from donna.memory.store import Document, MemoryStore

logger = structlog.get_logger()

SOURCE_TYPE = "vault"


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
                    if change in (Change.added, Change.modified):
                        await self._ingest_path(rel)
                    elif change == Change.deleted:
                        await self._store.delete(
                            source_type=SOURCE_TYPE,
                            source_id=rel,
                            user_id=self._user_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "vault_watch_event_failed",
                        path=rel,
                        reason=str(exc),
                    )

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
