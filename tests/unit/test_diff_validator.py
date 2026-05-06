"""Unit tests for slice 21 DiffValidator + GitRepo helpers.

The validator is purely-functional; the GitRepo helpers each use a
temp git repo created via :class:`donna.integrations.git_repo.GitRepo`
on a tmp_path.

Realizes acceptance for docs/superpowers/specs/manual-escalation.md
§10.3 rows 3 & 5 (out-of-scope rejection, working-tree exclusion).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.cost.diff_validator import DiffValidator
from donna.integrations.git_repo import GitRepo

# ---------------------------------------------------------------------------
# DiffValidator
# ---------------------------------------------------------------------------


def test_in_scope_paths_are_matched() -> None:
    target = {
        "skill": "skills/foo/**",
        "fixtures": "fixtures/foo/**",
    }
    diff = [
        "skills/foo/skill.yaml",
        "skills/foo/steps/extract.md",
        "fixtures/foo/case_one.json",
    ]
    result = DiffValidator.validate(diff, target)
    assert result.ok is True
    assert sorted(result.matched) == sorted(diff)
    assert result.out_of_scope == []


def test_out_of_scope_paths_are_rejected() -> None:
    target = {"skill": "skills/foo/**"}
    diff = [
        "skills/foo/skill.yaml",
        "src/donna/cost/budget.py",
        "README.md",
    ]
    result = DiffValidator.validate(diff, target)
    assert result.ok is False
    assert result.matched == ["skills/foo/skill.yaml"]
    assert sorted(result.out_of_scope) == ["README.md", "src/donna/cost/budget.py"]


def test_dotfile_additions_always_rejected() -> None:
    target = {"skill": "skills/foo/**", "github": ".github/**"}  # even if globbed
    diff = [".github/workflows/ci.yml", ".gitignore"]
    result = DiffValidator.validate(diff, target)
    # Even though .github/** would match, the dotfile rule rejects it.
    assert result.ok is False
    assert ".github/workflows/ci.yml" in result.out_of_scope
    assert ".gitignore" in result.out_of_scope


def test_empty_target_paths_fails_closed() -> None:
    result = DiffValidator.validate(["src/donna/cost/budget.py"], {})
    assert result.ok is False
    assert result.out_of_scope == ["src/donna/cost/budget.py"]


def test_recursive_glob_matches_nested_paths() -> None:
    target = {"skill": "skills/parse_task/**"}
    diff = [
        "skills/parse_task/skill.yaml",
        "skills/parse_task/steps/extract.md",
        "skills/parse_task/schemas/extract_v1.json",
    ]
    result = DiffValidator.validate(diff, target)
    assert result.ok is True
    assert len(result.matched) == 3


def test_non_recursive_glob_does_not_match_nested() -> None:
    target = {"single": "skills/foo/skill.yaml"}
    diff = ["skills/foo/skill.yaml", "skills/foo/steps/extract.md"]
    result = DiffValidator.validate(diff, target)
    assert result.matched == ["skills/foo/skill.yaml"]
    assert result.out_of_scope == ["skills/foo/steps/extract.md"]


# ---------------------------------------------------------------------------
# GitRepo helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path: Path) -> GitRepo:
    r = GitRepo(root=tmp_path / "repo")
    await r.init_if_missing()
    # Seed initial commit on main.
    (r.root / "README.md").write_text("# host repo\n")
    await r.commit(["README.md"], "initial")
    return r


async def test_branch_exists_returns_false_for_unknown(repo: GitRepo) -> None:
    assert await repo.branch_exists("not-a-real-branch") is False


async def test_branch_exists_returns_true_for_main(repo: GitRepo) -> None:
    # ``main`` is the default branch from init_if_missing().
    assert await repo.branch_exists("main") is True


async def test_rev_parse_resolves_main(repo: GitRepo) -> None:
    sha = await repo.rev_parse("refs/heads/main")
    assert sha is not None
    assert len(sha) == 40


async def test_rev_parse_returns_none_for_unknown(repo: GitRepo) -> None:
    assert await repo.rev_parse("refs/heads/xyzzy") is None


async def test_diff_names_returns_changed_files(repo: GitRepo) -> None:
    base_sha = await repo.head()
    assert base_sha is not None
    # Create a branch with one new file.
    await repo._run(["checkout", "-b", "escalation/test"])
    (repo.root / "skills").mkdir(exist_ok=True)
    (repo.root / "skills" / "foo.txt").write_text("hi\n")
    await repo.commit(["skills/foo.txt"], "add foo")
    diff = await repo.diff_names(base_sha, "escalation/test")
    assert "skills/foo.txt" in diff


async def test_diff_names_excludes_working_tree_changes(repo: GitRepo) -> None:
    """§10.3 row 5 — base..tip ignores working-tree mess."""
    base_sha = await repo.head()
    assert base_sha is not None
    await repo._run(["checkout", "-b", "escalation/test2"])
    (repo.root / "scope").mkdir()
    (repo.root / "scope" / "in.txt").write_text("inscope\n")
    await repo.commit(["scope/in.txt"], "add in-scope file")
    # Add an uncommitted file to the working tree.
    (repo.root / "out_of_scope.txt").write_text("uncommitted\n")
    diff = await repo.diff_names(base_sha, "escalation/test2")
    assert "scope/in.txt" in diff
    assert "out_of_scope.txt" not in diff


async def test_show_file_reads_committed_content(repo: GitRepo) -> None:
    await repo._run(["checkout", "-b", "show-test"])
    (repo.root / "data.txt").write_text("hello\n")
    await repo.commit(["data.txt"], "add data")
    out = await repo.show_file("show-test", "data.txt")
    assert out == "hello\n"
