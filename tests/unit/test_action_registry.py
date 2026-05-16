"""Tests for ActionRegistry loading, matching, and execution."""

from pathlib import Path

import pytest

from donna.chat.actions import ActionRegistry


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    config = tmp_path / "chat_actions.yaml"
    config.write_text("""
actions:
  query_tasks:
    description: "List tasks"
    domain: tasks
    safety: read
    handler: donna.chat.actions.tasks.query_tasks
    parameters:
      type: object
      properties:
        status: { type: string }
      required: []
  create_task:
    description: "Create a task"
    domain: tasks
    safety: write
    handler: donna.chat.actions.tasks.create_task
    parameters:
      type: object
      properties:
        title: { type: string }
      required: [title]
  execute_skill:
    description: "Run a skill"
    domain: skills
    safety: confirm
    handler: donna.chat.actions.skills.execute_skill
    parameters:
      type: object
      properties:
        skill_name: { type: string }
      required: [skill_name]
""")
    return config


def test_load_from_yaml(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    assert len(registry.list_actions()) == 3


def test_load_missing_file(tmp_path: Path) -> None:
    registry = ActionRegistry.from_yaml(tmp_path / "nonexistent.yaml")
    assert len(registry.list_actions()) == 0


def test_match_by_action_hint(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(action_hint="query_tasks")
    assert result is not None
    assert result.name == "query_tasks"
    assert result.safety == "read"


def test_match_by_domain_single(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(domain="skills")
    assert result is not None
    assert result.name == "execute_skill"


def test_match_by_domain_ambiguous(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(domain="tasks")
    assert result is None  # two tasks-domain actions, ambiguous


def test_get_by_name(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    assert registry.get("create_task") is not None
    assert registry.get("nonexistent") is None


def test_list_for_domain(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    tasks = registry.list_for_domain("tasks")
    assert len(tasks) == 2
    assert all(a.domain == "tasks" for a in tasks)


def test_pending_action_roundtrip() -> None:
    registry = ActionRegistry({})
    raw = registry.format_pending_action("create_task", {"title": "Test"})
    name, params = ActionRegistry.parse_pending_action(raw)
    assert name == "create_task"
    assert params == {"title": "Test"}
