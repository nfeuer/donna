"""Tests for action registry validation."""
from __future__ import annotations

from donna.config import (
    ActionDef,
    ActionParamDef,
    ReplyActionsConfig,
    ReplyMemoryConfig,
    ReplyPlanConfig,
)
from donna.replies.action_registry import ActionRegistry


def _make_config() -> ReplyActionsConfig:
    return ReplyActionsConfig(
        memory=ReplyMemoryConfig(),
        plan=ReplyPlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
            "create_task": ActionDef(
                description="Create task",
                handler="donna.replies.actions.task_actions.create_task",
                params={
                    "title": ActionParamDef(type="string"),
                    "domain": ActionParamDef(
                        type="string", enum=["work", "personal"], optional=True,
                    ),
                    "priority": ActionParamDef(type="int", default=2),
                },
                risk="medium",
            ),
        },
    )


class TestValidation:
    def test_valid_action_passes(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "mark_done", "params": {}})
        assert errors == []

    def test_unknown_action_rejected(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "fly_to_moon", "params": {}})
        assert any("unknown" in e.lower() for e in errors)

    def test_missing_required_param(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "create_task", "params": {}})
        assert any("title" in e for e in errors)

    def test_context_param_not_required_from_user(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "mark_done", "params": {}})
        assert errors == []

    def test_param_with_default_not_required(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action(
            {"action": "create_task", "params": {"title": "Do thing"}}
        )
        assert errors == []


class TestRenderForLLM:
    def test_render_produces_action_descriptions(self) -> None:
        reg = ActionRegistry(_make_config())
        text = reg.render_for_llm()
        assert "mark_done" in text
        assert "create_task" in text
        assert "Mark done" in text

    def test_render_includes_param_info(self) -> None:
        reg = ActionRegistry(_make_config())
        text = reg.render_for_llm()
        assert "title" in text
        assert "string" in text


class TestInjectContext:
    def test_inject_fills_context_params(self) -> None:
        reg = ActionRegistry(_make_config())
        action = {"action": "mark_done", "params": {}}
        context = {"task_id": "t-123"}
        filled = reg.inject_context(action, context)
        assert filled["params"]["task_id"] == "t-123"

    def test_inject_does_not_overwrite_explicit(self) -> None:
        reg = ActionRegistry(_make_config())
        action = {"action": "mark_done", "params": {"task_id": "t-explicit"}}
        context = {"task_id": "t-123"}
        filled = reg.inject_context(action, context)
        assert filled["params"]["task_id"] == "t-explicit"
