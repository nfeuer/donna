"""Slice 23 — boot-time validation of manual_escalation config (§10.7 row 3).

A task type that declares ``manual_escalation.mode='claude_code'`` MUST
also declare both ``target_paths`` (non-empty) and ``reference_module``.
We hard-fail at boot rather than allowing the gate to render a
worktree spec the user cannot act on.
"""

from __future__ import annotations

import pytest

from donna.config import (
    ManualEscalationConfigError,
    ManualEscalationTaskTypeConfig,
    TaskTypeEntry,
    TaskTypesConfig,
    validate_manual_escalation_config,
)


def _entry(manual: ManualEscalationTaskTypeConfig | None) -> TaskTypeEntry:
    return TaskTypeEntry(
        description="x",
        model="parser",
        prompt_template="prompts/x.md",
        output_schema="schemas/x.json",
        manual_escalation=manual,
    )


def test_passes_when_no_manual_escalation_blocks() -> None:
    cfg = TaskTypesConfig(task_types={"foo": _entry(None)})
    validate_manual_escalation_config(task_types=cfg)


def test_passes_when_chat_mode_has_no_target_paths() -> None:
    cfg = TaskTypesConfig(
        task_types={
            "chat_escalation": _entry(
                ManualEscalationTaskTypeConfig(mode="chat")
            )
        }
    )
    validate_manual_escalation_config(task_types=cfg)


def test_passes_when_claude_code_has_full_contract() -> None:
    cfg = TaskTypesConfig(
        task_types={
            "skill_auto_draft": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={"skill": "skills/{name}/**"},
                    reference_module="skills/parse_task/skill.yaml",
                )
            )
        }
    )
    validate_manual_escalation_config(task_types=cfg)


def test_raises_when_claude_code_missing_target_paths() -> None:
    cfg = TaskTypesConfig(
        task_types={
            "evolution": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths=None,
                    reference_module="skills/foo/skill.yaml",
                )
            )
        }
    )
    with pytest.raises(ManualEscalationConfigError) as exc:
        validate_manual_escalation_config(task_types=cfg)
    assert "evolution" in str(exc.value)
    assert "target_paths" in str(exc.value)


def test_raises_when_claude_code_missing_reference_module() -> None:
    cfg = TaskTypesConfig(
        task_types={
            "evolution": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={"skill": "skills/{name}/**"},
                    reference_module=None,
                )
            )
        }
    )
    with pytest.raises(ManualEscalationConfigError) as exc:
        validate_manual_escalation_config(task_types=cfg)
    assert "reference_module" in str(exc.value)


def test_collects_all_offenders_in_one_error() -> None:
    cfg = TaskTypesConfig(
        task_types={
            "a": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths=None,
                    reference_module=None,
                )
            ),
            "b": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={"x": "y"},
                    reference_module=None,
                )
            ),
            "c_ok": _entry(
                ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={"x": "y"},
                    reference_module="z",
                )
            ),
        }
    )
    with pytest.raises(ManualEscalationConfigError) as exc:
        validate_manual_escalation_config(task_types=cfg)
    msg = str(exc.value)
    assert "a:" in msg and "b:" in msg
    assert "c_ok" not in msg
