"""Tests for GET /admin/prompts/stats endpoint."""
from pathlib import Path

import pytest


@pytest.fixture
def _prompts_dir(tmp_path: Path) -> Path:
    """Create a minimal prompts/ tree."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "parse_task.md").write_text("# Parse Task\n{{ raw_text }}")
    (prompts / "classify_priority.md").write_text("# Classify\n{{ task_title }}")
    chat = prompts / "chat"
    chat.mkdir()
    (chat / "chat_respond.md").write_text("# Respond")
    return prompts


@pytest.fixture
def _config_dir(tmp_path: Path) -> Path:
    """Create minimal config/ YAML stubs."""
    config = tmp_path / "config"
    config.mkdir()

    (config / "task_types.yaml").write_text(
        """task_types:
  parse_task:
    model: parser
    prompt_template: prompts/parse_task.md
    output_schema: schemas/task_parse_output.json
    tools: []
  classify_priority:
    model: parser
    prompt_template: prompts/classify_priority.md
    output_schema: schemas/priority_output.json
    tools: [task_db_read]
"""
    )

    (config / "donna_models.yaml").write_text(
        """models:
  parser:
    provider: anthropic
    model: claude-sonnet-4-20250514
  local_parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q4_K_M
routing:
  parse_task:
    model: parser
  classify_priority:
    model: parser
"""
    )

    (config / "agents.yaml").write_text(
        """agents:
  pm:
    enabled: true
    timeout_seconds: 300
    autonomy: medium
    allowed_tools: [task_db_read, task_db_write]
"""
    )

    return config


def test_prompt_stats_returns_shape(
    _prompts_dir: Path, _config_dir: Path, tmp_path: Path,
) -> None:
    """Verify response includes all expected top-level keys."""
    from donna.api.routes.admin_config import _build_prompt_stats

    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts={},
    )
    assert stats["total"] == 3
    assert "chat" in stats["by_folder"]
    assert stats["by_folder"]["chat"] == 1
    assert stats["by_folder"]["root"] == 2
    assert isinstance(stats["most_invoked"], list)
    assert isinstance(stats["agent_coverage"], list)
    assert isinstance(stats["model_routing"], dict)
    assert isinstance(stats["recently_modified"], list)
    assert isinstance(stats["unused"], list)


def test_prompt_stats_invocation_ranking(
    _prompts_dir: Path, _config_dir: Path,
) -> None:
    """most_invoked should be sorted descending by invocation count."""
    from donna.api.routes.admin_config import _build_prompt_stats

    counts = {
        "parse_task": {"invocations": 100, "cost_usd": 0.50},
        "classify_priority": {"invocations": 200, "cost_usd": 1.00},
    }
    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts=counts,
    )
    assert len(stats["most_invoked"]) == 2
    assert stats["most_invoked"][0]["task_type"] == "classify_priority"
    assert stats["most_invoked"][0]["invocations"] == 200


def test_prompt_stats_unused_detection(
    _prompts_dir: Path, _config_dir: Path,
) -> None:
    """Prompts with no matching task_type or zero invocations should appear in unused."""
    from donna.api.routes.admin_config import _build_prompt_stats

    stats = _build_prompt_stats(
        prompts_dir=_prompts_dir,
        config_dir=_config_dir,
        invocation_counts={},
    )
    # chat_respond.md has no task_type mapping -> unused
    assert "chat/chat_respond.md" in stats["unused"]
