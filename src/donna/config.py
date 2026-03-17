"""Configuration loader for Donna.

Reads YAML config files and provides typed access. All extensible behavior
(model routing, task types, state machine, preferences) is driven by config,
not hardcoded in application logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """A single model alias definition."""

    provider: str
    model: str
    estimated_cost_per_1k_tokens: float | None = None


class RoutingEntry(BaseModel):
    """Routing config for a single task type."""

    model: str
    fallback: str | None = None
    confidence_threshold: float | None = None
    shadow: str | None = None


class CostConfig(BaseModel):
    """Budget thresholds."""

    monthly_budget_usd: float = 100.0
    daily_pause_threshold_usd: float = 20.0
    task_approval_threshold_usd: float = 5.0
    monthly_warning_pct: float = 0.90


class QualityMonitoringConfig(BaseModel):
    """Spot-check quality monitoring settings."""

    spot_check_rate: float = 0.05
    judge_model: str = "reasoner"
    judge_batch_schedule: str = "weekly"
    flag_threshold: float = 0.7
    enabled: bool = False


class ModelsConfig(BaseModel):
    """Top-level models configuration."""

    models: dict[str, ModelConfig]
    routing: dict[str, RoutingEntry]
    cost: CostConfig = Field(default_factory=CostConfig)
    quality_monitoring: QualityMonitoringConfig = Field(
        default_factory=QualityMonitoringConfig
    )


class TaskTypeEntry(BaseModel):
    """A single task type definition."""

    description: str
    model: str
    prompt_template: str
    output_schema: str
    tools: list[str] = Field(default_factory=list)
    shadow: str | None = None


class TaskTypesConfig(BaseModel):
    """Top-level task types configuration."""

    task_types: dict[str, TaskTypeEntry]


class TransitionEntry(BaseModel):
    """A single state machine transition."""

    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")
    trigger: str
    side_effects: list[str] = Field(default_factory=list)
    timeout_days: int | None = None

    model_config = {"populate_by_name": True}


class InvalidTransitionEntry(BaseModel):
    """An explicitly invalid transition."""

    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")
    reason: str
    except_states: list[str] = Field(default_factory=list, alias="except")

    model_config = {"populate_by_name": True}


class StateMachineConfig(BaseModel):
    """Task lifecycle state machine configuration."""

    states: list[str]
    initial_state: str
    transitions: list[TransitionEntry]
    invalid_transitions: list[InvalidTransitionEntry] = Field(default_factory=list)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def load_models_config(config_dir: Path) -> ModelsConfig:
    """Load model routing configuration."""
    data = load_yaml(config_dir / "donna_models.yaml")
    return ModelsConfig(**data)


def load_task_types_config(config_dir: Path) -> TaskTypesConfig:
    """Load task type registry."""
    data = load_yaml(config_dir / "task_types.yaml")
    return TaskTypesConfig(**data)


def load_state_machine_config(config_dir: Path) -> StateMachineConfig:
    """Load task lifecycle state machine."""
    data = load_yaml(config_dir / "task_states.yaml")
    return StateMachineConfig(**data)
