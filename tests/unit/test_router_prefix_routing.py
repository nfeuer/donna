"""Tests for ModelRouter._resolve_route prefix-match fallback (Wave 2 Task 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import load_models_config, load_task_types_config
from donna.models.router import ModelRouter, RoutingError


@pytest.fixture
def router(tmp_path):
    models_cfg = load_models_config(Path("config"))
    task_cfg = load_task_types_config(Path("config"))
    return ModelRouter(models_cfg, task_cfg, tmp_path)


def test_exact_key_match_takes_precedence(router) -> None:
    _, _, alias = router._resolve_route("parse_task")
    assert alias == "parser"


def test_prefix_match_fallback(router) -> None:
    _, _, alias = router._resolve_route("skill_step::product_watch::fetch_page")
    assert alias == "parser"


def test_skill_validation_prefix_routes(router) -> None:
    _, _, alias = router._resolve_route(
        "skill_validation::product_watch::extract_product_info"
    )
    assert alias == "parser"


def test_longest_prefix_wins(router) -> None:
    existing = router._models_config.routing["parse_task"]
    # Clone-and-override via type to dodge frozen-model issues.
    router._models_config.routing["skill_step::product_watch"] = type(existing)(
        model="reasoner",
    )
    _, _, alias = router._resolve_route("skill_step::product_watch::fetch_page")
    assert alias == "reasoner"


def test_no_match_still_raises(router) -> None:
    with pytest.raises(RoutingError):
        router._resolve_route("totally_unknown_prefix::something::else")
