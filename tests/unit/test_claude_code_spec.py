"""Unit tests for ClaudeCodeSpecBuilder (slice 21).

Realizes acceptance for docs/superpowers/specs/manual-escalation.md §5.3
(spec content) and §9 (template path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import TaskTypeManualEscalation
from donna.cost.claude_code_spec import (
    ClaudeCodeSpecBuilder,
    expand_workspace_path,
)


@pytest.fixture
def builder(tmp_path: Path) -> ClaudeCodeSpecBuilder:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    return ClaudeCodeSpecBuilder(
        prompt_dir=Path("prompts/escalation"),
        workspace_path=workspace,
        host_repo_path=host,
        worktree_root=workspace / "worktrees",
        dashboard_base_url="http://localhost:8080/",
        iteration_limit=3,
    )


@pytest.fixture
def manual() -> TaskTypeManualEscalation:
    return TaskTypeManualEscalation(
        mode="claude_code",
        target_paths={
            "skill": "skills/{name}/**",
            "fixtures": "fixtures/{name}/**",
        },
        reference_module="skills/parse_task/skill.yaml",
        forbidden_patterns=["import anthropic", "DONNA_API_KEY"],
    )


def test_render_writes_spec_to_workspace(
    builder: ClaudeCodeSpecBuilder,
    manual: TaskTypeManualEscalation,
    tmp_path: Path,
) -> None:
    rendered = builder.render(
        correlation_id="01923456-7890-7abc-def0-123456789abc",
        task_type="skill_auto_draft",
        capability_name="bookmark",
        manual=manual,
        base_sha="abc1234567",
        task_summary="Build the bookmark skill.",
        acceptance_criteria=["pass rate >= 0.8"],
    )
    assert rendered.path.is_file()
    assert rendered.path.parent.name == "escalations"
    assert rendered.body == rendered.path.read_text()
    assert "01923456" in rendered.branch_name
    assert "bookmark" in rendered.branch_name


def test_render_substitutes_name_into_target_paths(
    builder: ClaudeCodeSpecBuilder,
    manual: TaskTypeManualEscalation,
) -> None:
    rendered = builder.render(
        correlation_id="01923456-aaaa-7bbb-cccc-ddddeeeeffff",
        task_type="skill_auto_draft",
        capability_name="news_check",
        manual=manual,
        base_sha="cafe1234",
        task_summary="x",
        acceptance_criteria=["x"],
    )
    assert rendered.target_paths["skill"] == "skills/news_check/**"
    assert rendered.target_paths["fixtures"] == "fixtures/news_check/**"
    assert "skills/news_check/**" in rendered.body
    assert "fixtures/news_check/**" in rendered.body


def test_render_includes_worktree_command(
    builder: ClaudeCodeSpecBuilder,
    manual: TaskTypeManualEscalation,
) -> None:
    rendered = builder.render(
        correlation_id="01923456-aaaa-7bbb-cccc-ddddeeeeffff",
        task_type="skill_auto_draft",
        capability_name="news_check",
        manual=manual,
        base_sha="cafe1234567",
        task_summary="x",
        acceptance_criteria=["x"],
    )
    assert rendered.worktree_command.startswith("git worktree add -b ")
    assert rendered.branch_name in rendered.worktree_command
    assert "cafe1234567" in rendered.worktree_command
    assert rendered.worktree_command in rendered.body


def test_render_emits_forbidden_patterns(
    builder: ClaudeCodeSpecBuilder,
    manual: TaskTypeManualEscalation,
) -> None:
    rendered = builder.render(
        correlation_id="01923456-aaaa-7bbb-cccc-ddddeeeeffff",
        task_type="skill_auto_draft",
        capability_name="news_check",
        manual=manual,
        base_sha="cafe1234",
        task_summary="x",
        acceptance_criteria=["x"],
    )
    assert "import anthropic" in rendered.body
    assert "DONNA_API_KEY" in rendered.body


def test_render_rejects_unsafe_capability_name(
    builder: ClaudeCodeSpecBuilder,
    manual: TaskTypeManualEscalation,
) -> None:
    with pytest.raises(ValueError, match="must match"):
        builder.render(
            correlation_id="01923456-aaaa-7bbb-cccc-ddddeeeeffff",
            task_type="skill_auto_draft",
            capability_name="../etc/passwd",
            manual=manual,
            base_sha="cafe1234",
            task_summary="x",
            acceptance_criteria=["x"],
        )


def test_expand_workspace_path_substitutes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONNA_WORKSPACE_PATH", "/srv/donna")
    p = expand_workspace_path("${DONNA_WORKSPACE_PATH}/worktrees")
    assert p == Path("/srv/donna/worktrees")
