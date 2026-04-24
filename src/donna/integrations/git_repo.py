"""Subprocess-based git wrapper for the Donna vault (slice 12).

Purpose: give :class:`~donna.integrations.vault.VaultWriter` a minimal,
dependency-free way to commit every successful mutation and to revert
them with a single call. We intentionally avoid GitPython — one subprocess
per operation is fine at human-scale write rates, and it removes a
transitive dependency with a history of API churn.

Safety:

- `user.name` / `user.email` are set on the local repo only (never via
  `--global`). Each commit is explicitly authored via ``-c`` overrides
  so the local config can drift without changing author metadata.
- ``revert`` uses ``git revert`` (never ``git reset``) so the audit
  trail is preserved.
- No network operations. The vault is a local-only repo in this slice.
- GPG / SSH commit signing is explicitly **disabled** via
  ``-c commit.gpgsign=false`` and ``-c tag.gpgsign=false`` on every
  write. Vault commits never leave the homelab, so signing adds no
  value and makes the integration brittle on any host where
  ``user.signingkey`` / ``gpg.program`` is set globally. This is a
  design decision for this internal audit-trail repo, not a global
  bypass.

See `spec_v3.md §7.3` (agent safety constraints) and the slice brief
at `slices/slice_12_vault_plumbing.md`.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()


class GitRepoError(RuntimeError):
    """Raised when a git subprocess exits non-zero."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        super().__init__(
            f"git {' '.join(command)} exited {returncode}: {stderr.strip()}"
        )
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class GitCommit:
    """Lightweight commit record returned by :meth:`GitRepo.log`."""

    sha: str
    message: str


class GitRepo:
    """Thin async wrapper around ``git`` for a single local repo.

    All methods run ``git`` via :func:`asyncio.to_thread` so the event
    loop isn't blocked. Methods raise :class:`GitRepoError` when the
    underlying process exits non-zero.
    """

    def __init__(
        self,
        root: Path,
        author_name: str = "Donna",
        author_email: str = "donna@homelab.local",
    ) -> None:
        self._root = Path(root)
        self._author_name = author_name
        self._author_email = author_email

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    # Base ``-c`` overrides applied to every invocation. GPG/SSH commit
    # signing is disabled for this internal repo — see module docstring.
    _BASE_OVERRIDES: tuple[str, ...] = (
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
    )

    def _run_sync(self, args: list[str]) -> str:
        """Run ``git <args>`` in the repo. Returns stdout; raises on failure."""
        full = ["git", "-C", str(self._root), *self._BASE_OVERRIDES, *args]
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitRepoError(full, proc.returncode, proc.stderr)
        return proc.stdout

    async def _run(self, args: list[str]) -> str:
        return await asyncio.to_thread(self._run_sync, args)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def init_if_missing(self) -> bool:
        """Ensure the repo exists. Returns True if newly initialised.

        Sets local ``user.name`` / ``user.email`` on first init. Existing
        repos are left untouched — the user may have chosen different
        local config and we respect that.
        """
        if (self._root / ".git").is_dir():
            return False
        self._root.mkdir(parents=True, exist_ok=True)
        # ``git init`` with a deterministic default branch so new vaults
        # don't depend on the host's init.defaultBranch setting.
        await asyncio.to_thread(
            subprocess.run,
            [
                "git",
                "-C",
                str(self._root),
                *self._BASE_OVERRIDES,
                "init",
                "--initial-branch=main",
            ],
            capture_output=True,
            check=True,
        )
        await self._run(["config", "user.name", self._author_name])
        await self._run(["config", "user.email", self._author_email])
        logger.info("vault_git_initialised", root=str(self._root))
        return True

    async def commit(
        self,
        paths: list[str],
        message: str,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> str:
        """Stage the given relative paths and create a commit. Returns the SHA.

        Paths are interpreted relative to the repo root. The commit author
        is pinned via ``-c`` so it cannot be overridden by local config.
        """
        if not paths:
            raise ValueError("commit() requires at least one path")
        name = author_name or self._author_name
        email = author_email or self._author_email

        await self._run(["add", "--", *paths])
        await self._run(
            [
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={email}",
                "commit",
                "-m",
                message,
                "--",
                *paths,
            ]
        )
        sha = (await self._run(["rev-parse", "HEAD"])).strip()
        return sha

    async def revert(self, n: int = 1) -> list[str]:
        """Revert the last ``n`` commits (newest first). Returns new revert SHAs.

        Uses ``git revert --no-edit`` so the operation is unattended.
        Reverting preserves history — suitable for the ``vault_undo_last``
        tool where the user wants to undo an agent write without rewriting
        the log.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        heads = (await self._run(["rev-list", "-n", str(n), "HEAD"])).split()
        revert_shas: list[str] = []
        for sha in heads:  # newest first is what rev-list returns
            await self._run(
                [
                    "-c",
                    f"user.name={self._author_name}",
                    "-c",
                    f"user.email={self._author_email}",
                    "revert",
                    "--no-edit",
                    sha,
                ]
            )
            revert_sha = (await self._run(["rev-parse", "HEAD"])).strip()
            revert_shas.append(revert_sha)
        return revert_shas

    async def log(self, limit: int = 20) -> list[GitCommit]:
        """Return the most recent ``limit`` commits (newest first)."""
        out = await self._run(
            ["log", f"-n{limit}", "--pretty=format:%H%x1f%s"]
        )
        commits: list[GitCommit] = []
        for line in out.splitlines():
            if not line:
                continue
            sha, _, message = line.partition("\x1f")
            commits.append(GitCommit(sha=sha, message=message))
        return commits

    async def head(self) -> str | None:
        """Return the current HEAD SHA, or None if the repo has no commits."""
        try:
            return (await self._run(["rev-parse", "HEAD"])).strip()
        except GitRepoError:
            return None
