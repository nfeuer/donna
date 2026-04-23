"""GitRepo wrapper: init, commit, revert against a real git binary."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.integrations.git_repo import GitRepo


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path, author_name="Donna", author_email="d@example.com")
    assert await repo.init_if_missing() is True
    assert (tmp_path / ".git").is_dir()
    # Second call is a no-op and returns False.
    assert await repo.init_if_missing() is False


@pytest.mark.asyncio
async def test_commit_and_revert_cycle(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path, author_name="Donna", author_email="d@example.com")
    await repo.init_if_missing()

    (tmp_path / "a.md").write_text("alpha\n")
    sha1 = await repo.commit(["a.md"], "add alpha")
    assert len(sha1) == 40

    (tmp_path / "a.md").write_text("alpha-v2\n")
    sha2 = await repo.commit(["a.md"], "edit alpha")
    assert sha2 != sha1

    # revert the last commit; file should go back to "alpha\n".
    reverts = await repo.revert(1)
    assert len(reverts) == 1
    assert (tmp_path / "a.md").read_text() == "alpha\n"

    log = await repo.log(limit=5)
    assert log[0].message.lower().startswith("revert")
    shas = {entry.sha for entry in log}
    assert sha1 in shas
    assert sha2 in shas


@pytest.mark.asyncio
async def test_head_returns_none_before_any_commit(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path, author_name="D", author_email="d@example.com")
    await repo.init_if_missing()
    assert await repo.head() is None
