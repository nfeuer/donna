"""Unit tests for configuration loading.

Validates that YAML configs parse correctly into typed Pydantic models.
"""

from pathlib import Path

import pytest

from donna.config import (
    load_models_config,
    load_state_machine_config,
    load_task_types_config,
)

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


@pytest.mark.skipif(
    not (CONFIG_DIR / "donna_models.yaml").exists(),
    reason="Config files not found — run from repo root",
)
class TestLoadModelsConfig:
    def test_loads_without_error(self) -> None:
        config = load_models_config(CONFIG_DIR)
        assert config is not None

    def test_has_required_model_aliases(self) -> None:
        config = load_models_config(CONFIG_DIR)
        assert "parser" in config.models
        assert "reasoner" in config.models
        assert "fallback" in config.models

    def test_has_routing_entries(self) -> None:
        config = load_models_config(CONFIG_DIR)
        assert "parse_task" in config.routing
        assert "classify_priority" in config.routing
        assert "generate_digest" in config.routing

    def test_cost_thresholds(self) -> None:
        config = load_models_config(CONFIG_DIR)
        assert config.cost.monthly_budget_usd == 100.0
        assert config.cost.daily_pause_threshold_usd == 20.0

    def test_shadow_config(self) -> None:
        config = load_models_config(CONFIG_DIR)
        digest = config.routing.get("generate_digest")
        assert digest is not None
        assert digest.shadow == "reasoner"

    def test_quality_monitoring_disabled_phase1(self) -> None:
        config = load_models_config(CONFIG_DIR)
        assert config.quality_monitoring.enabled is False


@pytest.mark.skipif(
    not (CONFIG_DIR / "task_types.yaml").exists(),
    reason="Config files not found — run from repo root",
)
class TestLoadTaskTypesConfig:
    def test_loads_without_error(self) -> None:
        config = load_task_types_config(CONFIG_DIR)
        assert config is not None

    def test_has_core_task_types(self) -> None:
        config = load_task_types_config(CONFIG_DIR)
        assert "parse_task" in config.task_types
        assert "classify_priority" in config.task_types
        assert "generate_digest" in config.task_types

    def test_task_type_has_prompt_template(self) -> None:
        config = load_task_types_config(CONFIG_DIR)
        parse = config.task_types["parse_task"]
        assert parse.prompt_template == "prompts/parse_task.md"

    def test_tool_access_defined(self) -> None:
        config = load_task_types_config(CONFIG_DIR)
        # parse_task has no tools
        assert config.task_types["parse_task"].tools == []
        # classify_priority has task_db_read
        assert "task_db_read" in config.task_types["classify_priority"].tools


@pytest.mark.skipif(
    not (CONFIG_DIR / "task_states.yaml").exists(),
    reason="Config files not found — run from repo root",
)
class TestLoadStateMachineConfig:
    def test_loads_without_error(self) -> None:
        config = load_state_machine_config(CONFIG_DIR)
        assert config is not None

    def test_has_all_states(self) -> None:
        config = load_state_machine_config(CONFIG_DIR)
        expected = {
            "backlog", "scheduled", "in_progress", "blocked",
            "waiting_input", "done", "cancelled",
        }
        assert set(config.states) == expected

    def test_initial_state_is_backlog(self) -> None:
        config = load_state_machine_config(CONFIG_DIR)
        assert config.initial_state == "backlog"

    def test_has_transitions(self) -> None:
        config = load_state_machine_config(CONFIG_DIR)
        assert len(config.transitions) > 0

    def test_has_invalid_transitions(self) -> None:
        config = load_state_machine_config(CONFIG_DIR)
        assert len(config.invalid_transitions) > 0
