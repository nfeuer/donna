"""Obsidian-compatible vault client + writer for Donna (slice 12).

This module owns the **only** read/write path into the markdown vault.
Agents dispatch through the five tools registered in
``donna.skills.tools`` (``vault_read``, ``vault_write``, ``vault_list``,
``vault_link``, ``vault_undo_last``); each tool calls into this module.

Design:

- :class:`VaultClient` is read-only: ``read``, ``list``, ``stat``.
  Frontmatter is parsed via ``python-frontmatter``.
- :class:`VaultWriter` is the sole mutation path. It enforces the full
  safety envelope (path containment, extension, size, optimistic
  concurrency, frontmatter preservation) **before** touching disk, and
  records every successful mutation as a git commit via
  :class:`donna.integrations.git_repo.GitRepo`.
- Both are async; blocking file I/O is funnelled through
  :func:`asyncio.to_thread` to keep the event loop free (mirrors
  :class:`donna.integrations.gmail.GmailClient`).

FTS5 search is deferred to slice 13 per the slice brief — it will live
in the new memory DB.

See ``slices/slice_12_vault_plumbing.md`` and
``spec_v3.md §1.3 / §3.2.4 / §7.3 / §17``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import frontmatter
import structlog

from donna.config import MemoryConfig
from donna.integrations.git_repo import GitRepo

logger = structlog.get_logger()


# Rough wikilink pattern: ``[[Some Note]]`` or ``[[Folder/Note|alias]]``.
# We do not resolve aliases or sub-headings in this slice.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")


@dataclasses.dataclass(frozen=True)
class VaultNote:
    """A single markdown note loaded from the vault."""

    path: str  # forward-slash relative path from vault root (e.g. "Inbox/foo.md")
    content: str  # body (without frontmatter)
    frontmatter: dict[str, Any]
    mtime: float  # seconds since epoch
    size: int  # bytes on disk


class VaultError(RuntimeError):
    """Base class for vault errors."""


class VaultReadError(VaultError):
    """Raised when reading a note fails (missing, outside root, etc.)."""


class VaultWriteError(VaultError):
    """Raised when a write is rejected by the safety envelope.

    ``reason`` is a short machine-readable tag: ``"conflict"``,
    ``"path_escape"``, ``"not_markdown"``, ``"too_large"``,
    ``"outside_allowlist"``, ``"sensitive"``, ``"missing"``.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _to_posix_rel(root: Path, abs_path: Path) -> str:
    return str(abs_path.relative_to(root).as_posix())


def _resolve_safe_path(root: Path, rel: str, allowlist: list[str]) -> Path:
    """Resolve ``rel`` against ``root`` and assert containment.

    Rejects: absolute paths, ``..`` traversal, non-``.md`` extensions,
    symlinks whose realpath escapes the vault root, and any path whose
    top-level directory is not in ``allowlist``.
    """
    if not rel or rel.strip() == "":
        raise VaultWriteError("path_escape", "empty path")
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise VaultWriteError("path_escape", f"absolute path not allowed: {rel}")
    if any(part == ".." for part in rel_path.parts):
        raise VaultWriteError("path_escape", f"parent traversal not allowed: {rel}")
    if rel_path.suffix.lower() != ".md":
        raise VaultWriteError("not_markdown", f"only .md allowed, got {rel_path.suffix!r}")

    # Resolve through any symlinks, then check containment via realpath.
    root_real = os.path.realpath(root)
    abs_path = (root / rel_path).resolve(strict=False)
    abs_real = os.path.realpath(abs_path)
    try:
        rel_to_root = os.path.relpath(abs_real, root_real)
    except ValueError as exc:
        raise VaultWriteError("path_escape", f"cannot resolve under root: {rel}") from exc
    if rel_to_root.startswith("..") or os.path.isabs(rel_to_root):
        raise VaultWriteError(
            "path_escape", f"resolved path escapes vault root: {rel}"
        )

    top = rel_path.parts[0]
    if top not in allowlist:
        raise VaultWriteError(
            "outside_allowlist",
            f"top-level folder {top!r} not in safety.path_allowlist",
        )
    return abs_path


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------


def _parse_note(text: str) -> tuple[dict[str, Any], str]:
    """Parse ``text`` as a frontmatter-prefixed markdown note.

    Returns ``(frontmatter_dict, body)``. If no frontmatter block is
    present, the dict is empty and the body is the full text.
    """
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


def _serialise_note(metadata: Mapping[str, Any], body: str) -> str:
    """Inverse of :func:`_parse_note`. Omits the frontmatter block if empty."""
    if not metadata:
        return body if body.endswith("\n") else body + "\n"
    post = frontmatter.Post(body, **dict(metadata))
    serialised: str = frontmatter.dumps(post)
    return serialised if serialised.endswith("\n") else serialised + "\n"


# ---------------------------------------------------------------------------
# VaultClient (read-only)
# ---------------------------------------------------------------------------


class VaultClient:
    """Read-only side of the vault. Thread-safe; no per-client state."""

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._root = Path(config.vault.root)

    @property
    def root(self) -> Path:
        return self._root

    def _ignored(self, rel: str) -> bool:
        import fnmatch

        return any(
            fnmatch.fnmatch(rel, pattern)
            for pattern in self._config.vault.ignore_globs
        )

    # -- sync helpers run via asyncio.to_thread ------------------------

    def _read_sync(self, rel: str) -> VaultNote:
        # Read goes through the same safety envelope the writer uses, minus
        # the allowlist check — we allow reading any `.md` file under the
        # vault root (e.g. README.md, Templates/*.md), but still reject
        # traversal or symlink escape.
        rel_path = Path(rel)
        if rel_path.is_absolute() or any(p == ".." for p in rel_path.parts):
            raise VaultReadError(f"path_escape: {rel}")
        if rel_path.suffix.lower() != ".md":
            raise VaultReadError(f"not_markdown: {rel}")

        abs_path = (self._root / rel_path).resolve(strict=False)
        root_real = os.path.realpath(self._root)
        abs_real = os.path.realpath(abs_path)
        rel_to_root = os.path.relpath(abs_real, root_real)
        if rel_to_root.startswith(".."):
            raise VaultReadError(f"path_escape: {rel}")

        if not abs_path.exists():
            raise VaultReadError(f"missing: {rel}")

        stat = abs_path.stat()
        text = abs_path.read_text(encoding="utf-8")
        meta, body = _parse_note(text)
        return VaultNote(
            path=_to_posix_rel(self._root, abs_path),
            content=body,
            frontmatter=meta,
            mtime=stat.st_mtime,
            size=stat.st_size,
        )

    def _list_sync(self, folder: str, recursive: bool) -> list[str]:
        base = self._root if folder in ("", ".") else (self._root / folder)
        if not base.is_dir():
            return []
        results: list[str] = []
        glob = base.rglob("*.md") if recursive else base.glob("*.md")
        for p in glob:
            if not p.is_file():
                continue
            rel = _to_posix_rel(self._root, p)
            if self._ignored(rel):
                continue
            results.append(rel)
        results.sort()
        return results

    def _stat_sync(self, rel: str) -> tuple[float, int]:
        abs_path = (self._root / rel).resolve(strict=False)
        if not abs_path.exists():
            raise VaultReadError(f"missing: {rel}")
        s = abs_path.stat()
        return s.st_mtime, s.st_size

    # -- async public API ---------------------------------------------

    async def read(self, path: str) -> VaultNote:
        """Read a note at a forward-slash relative path."""
        return await asyncio.to_thread(self._read_sync, path)

    async def extract_links(self, path: str) -> list[str]:
        """Return the list of ``[[wikilink]]`` targets in a note's body."""
        note = await self.read(path)
        return [m.group(1).strip() for m in _WIKILINK_RE.finditer(note.content)]

    async def stat(self, path: str) -> tuple[float, int]:
        """Return ``(mtime, size)`` for an existing note."""
        return await asyncio.to_thread(self._stat_sync, path)

    # ``list`` is defined last so it shadows the builtin only after the
    # other method signatures (which use ``list[str]`` in annotations)
    # are resolved. Keeping the spec-required name ``list`` without
    # forcing string annotations elsewhere.
    async def list(self, folder: str = "", recursive: bool = True) -> list[str]:  # noqa: A003
        """List relative paths under ``folder`` (vault-root when empty)."""
        return await asyncio.to_thread(self._list_sync, folder, recursive)


# ---------------------------------------------------------------------------
# VaultWriter (mutation)
# ---------------------------------------------------------------------------


class VaultWriter:
    """Sole write path into the vault. Every mutation is a git commit.

    Enforces the safety envelope documented in ``spec_v3.md §7.3`` and
    the slice brief:

    1. Path must resolve under the vault root (no ``..``, no absolute,
       no symlink escape).
    2. Extension must be ``.md``.
    3. Top-level folder must be in ``safety.path_allowlist``.
    4. Payload size must be <= ``safety.max_note_bytes``.
    5. If ``expected_mtime`` is supplied and the on-disk mtime differs,
       raise :class:`VaultWriteError` with ``reason="conflict"`` **before**
       writing.
    6. If the target already exists with frontmatter and the new content
       omits it, the existing frontmatter is preserved on keys the new
       body does not supply.
    7. Every successful write/delete/move calls
       :meth:`GitRepo.commit` with a structured message.
    """

    _COMMIT_PREFIX = "donna(slice12)"

    def __init__(
        self,
        config: MemoryConfig,
        git: GitRepo,
        client: VaultClient | None = None,
    ) -> None:
        self._config = config
        self._git = git
        self._client = client or VaultClient(config)
        self._root = Path(config.vault.root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def git(self) -> GitRepo:
        return self._git

    # -- setup --------------------------------------------------------

    async def ensure_ready(self) -> None:
        """Create the vault root, the allowlisted folders, and the git repo.

        Idempotent — safe to call on every boot. Directories that already
        exist are left in place.
        """
        await asyncio.to_thread(self._ensure_dirs_sync)
        await self._git.init_if_missing()

    def _ensure_dirs_sync(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for folder in self._config.safety.path_allowlist:
            (self._root / folder).mkdir(parents=True, exist_ok=True)

    # -- helpers ------------------------------------------------------

    def _resolve(self, rel: str) -> Path:
        return _resolve_safe_path(
            self._root, rel, self._config.safety.path_allowlist
        )

    def _check_size(self, content: str) -> None:
        size = len(content.encode("utf-8"))
        cap = self._config.safety.max_note_bytes
        if size > cap:
            raise VaultWriteError(
                "too_large",
                f"payload {size} bytes exceeds max_note_bytes={cap}",
            )

    def _check_sensitive(self, existing_meta: Mapping[str, Any]) -> None:
        key = self._config.safety.sensitive_frontmatter_key
        if key and existing_meta.get(key):
            raise VaultWriteError(
                "sensitive",
                f"note has {key}=true; refusing to overwrite via agent tools",
            )

    @staticmethod
    def _merge_frontmatter(
        existing: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Merge on overwrite: new body wins on body; existing frontmatter
        wins on keys the incoming payload does not supply."""
        merged = dict(existing)
        merged.update(dict(incoming))
        return merged

    # -- public API ---------------------------------------------------

    async def write(
        self,
        path: str,
        content: str,
        expected_mtime: float | None = None,
        message: str | None = None,
    ) -> str:
        """Create or overwrite a note. Returns the commit SHA.

        ``content`` may or may not include its own frontmatter block —
        :func:`_parse_note` handles both. If the target exists and has
        a sensitive-key frontmatter, the write is refused.
        """
        self._check_size(content)
        abs_path = self._resolve(path)

        existing_meta: dict[str, Any] = {}
        if abs_path.exists():
            current_mtime = abs_path.stat().st_mtime
            if expected_mtime is not None and current_mtime != expected_mtime:
                raise VaultWriteError(
                    "conflict",
                    f"on-disk mtime {current_mtime} != expected {expected_mtime}",
                )
            existing_text = await asyncio.to_thread(
                abs_path.read_text, "utf-8"
            )
            existing_meta, _ = _parse_note(existing_text)
            self._check_sensitive(existing_meta)

        incoming_meta, body = _parse_note(content)
        merged_meta = self._merge_frontmatter(existing_meta, incoming_meta)
        payload = _serialise_note(merged_meta, body)

        def _write_sync() -> None:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write_sync)

        rel = _to_posix_rel(self._root, abs_path)
        commit_message = message or f"{self._COMMIT_PREFIX}: write {rel}"
        sha = await self._git.commit([rel], commit_message)
        logger.info("vault_write", path=rel, sha=sha, bytes=len(payload))
        return sha

    async def delete(self, path: str, message: str | None = None) -> str:
        """Delete a note. Returns the commit SHA."""
        abs_path = self._resolve(path)
        if not abs_path.exists():
            raise VaultWriteError("missing", f"cannot delete missing note: {path}")

        # Read existing frontmatter for the sensitive-key check.
        existing_text = await asyncio.to_thread(abs_path.read_text, "utf-8")
        existing_meta, _ = _parse_note(existing_text)
        self._check_sensitive(existing_meta)

        await asyncio.to_thread(abs_path.unlink)
        rel = _to_posix_rel(self._root, abs_path)
        commit_message = message or f"{self._COMMIT_PREFIX}: delete {rel}"
        sha = await self._git.commit([rel], commit_message)
        logger.info("vault_delete", path=rel, sha=sha)
        return sha

    async def move(
        self, src: str, dst: str, message: str | None = None
    ) -> str:
        """Move a note. Returns the commit SHA."""
        src_abs = self._resolve(src)
        dst_abs = self._resolve(dst)
        if not src_abs.exists():
            raise VaultWriteError("missing", f"source does not exist: {src}")
        if dst_abs.exists():
            raise VaultWriteError(
                "conflict", f"destination already exists: {dst}"
            )

        def _move_sync() -> None:
            dst_abs.parent.mkdir(parents=True, exist_ok=True)
            src_abs.rename(dst_abs)

        await asyncio.to_thread(_move_sync)
        src_rel = _to_posix_rel(self._root, src_abs)
        dst_rel = _to_posix_rel(self._root, dst_abs)
        commit_message = message or f"{self._COMMIT_PREFIX}: move {src_rel} -> {dst_rel}"
        sha = await self._git.commit([src_rel, dst_rel], commit_message)
        logger.info("vault_move", src=src_rel, dst=dst_rel, sha=sha)
        return sha

    async def undo_last(self, n: int = 1) -> list[str]:
        """Revert the last ``n`` commits. Returns the new revert SHAs."""
        shas = await self._git.revert(n)
        logger.info("vault_undo_last", n=n, revert_shas=shas)
        return shas
