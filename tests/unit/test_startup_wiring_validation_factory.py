"""Tests for the validation_executor_factory wiring in assemble_skill_system."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import SkillSystemConfig
from donna.skills.startup_wiring import assemble_skill_system
from donna.skills.validation_executor import ValidationExecutor


def _make_deps():
    fake_router = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()
    fake_budget = MagicMock()
    fake_budget.check_pre_call = AsyncMock(return_value=None)

    async def _notifier(msg: str) -> None:
        pass

    return fake_router, fake_conn, fake_budget, _notifier


def test_default_factory_produces_validation_executor() -> None:
    fake_router, fake_conn, fake_budget, notifier = _make_deps()
    bundle = assemble_skill_system(
        connection=fake_conn,
        model_router=fake_router,
        budget_guard=fake_budget,
        notifier=notifier,
        config=SkillSystemConfig(enabled=True),
    )
    assert bundle is not None
    # AutoDrafter and Evolver both get the factory.
    validator = bundle.auto_drafter._executor_factory()
    assert isinstance(validator, ValidationExecutor)
    validator2 = bundle.evolver._executor_factory()
    assert isinstance(validator2, ValidationExecutor)


def test_none_factory_falls_back_to_default() -> None:
    """Explicit None routes to default factory (no vacuous pass anywhere)."""
    fake_router, fake_conn, fake_budget, notifier = _make_deps()
    bundle = assemble_skill_system(
        connection=fake_conn, model_router=fake_router,
        budget_guard=fake_budget, notifier=notifier,
        config=SkillSystemConfig(enabled=True),
        validation_executor_factory=None,
    )
    assert isinstance(bundle.auto_drafter._executor_factory(), ValidationExecutor)
    assert isinstance(bundle.evolver._executor_factory(), ValidationExecutor)


def test_custom_factory_overrides_default() -> None:
    fake_router, fake_conn, fake_budget, notifier = _make_deps()
    sentinel = object()

    def _custom_factory():
        return sentinel

    bundle = assemble_skill_system(
        connection=fake_conn, model_router=fake_router,
        budget_guard=fake_budget, notifier=notifier,
        config=SkillSystemConfig(enabled=True),
        validation_executor_factory=_custom_factory,
    )
    assert bundle.auto_drafter._executor_factory() is sentinel
    assert bundle.evolver._executor_factory() is sentinel
