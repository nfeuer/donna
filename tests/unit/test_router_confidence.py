"""Tests for the router confidence-threshold accessor."""

from __future__ import annotations

from pathlib import Path

from donna.config import (
    ModelConfig,
    ModelsConfig,
    RoutingEntry,
    TaskTypesConfig,
)
from donna.models.router import ModelRouter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _router() -> ModelRouter:
    cfg = ModelsConfig(
        models={"parser": ModelConfig(provider="anthropic", model="x")},
        routing={
            "parse_task": RoutingEntry(model="parser", confidence_threshold=0.7),
            "no_threshold": RoutingEntry(model="parser"),
        },
    )
    return ModelRouter(cfg, TaskTypesConfig(task_types={}), PROJECT_ROOT)


def test_returns_configured_threshold() -> None:
    assert _router().confidence_threshold_for("parse_task") == 0.7


def test_returns_none_when_unset() -> None:
    assert _router().confidence_threshold_for("no_threshold") is None


def test_returns_none_for_unknown_task() -> None:
    assert _router().confidence_threshold_for("bogus") is None
