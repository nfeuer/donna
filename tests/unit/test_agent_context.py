"""R3 (§7.2 resolution): AgentContext keeps only router/user_id/project_root.

The unused ``db`` and ``tool_registry`` fields were stripped (a raw DB handle let
an agent bypass the tool-validation seam — CLAUDE.md principle #6). The shared
result/record dataclasses are retained. See
``docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md`` §5 R3.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from donna.agents.base import AgentContext, AgentResult, ToolCallRecord


def test_agent_context_constructs_with_live_fields_only():
    ctx = AgentContext(
        router=MagicMock(),
        user_id="nick",
        project_root=Path("/tmp/donna"),
    )
    assert ctx.user_id == "nick"
    assert ctx.project_root == Path("/tmp/donna")


def test_agent_context_no_longer_has_db_or_tool_registry():
    field_names = {f.name for f in dataclasses.fields(AgentContext)}
    assert field_names == {"router", "user_id", "project_root"}
    assert "db" not in field_names
    assert "tool_registry" not in field_names


def test_agent_context_rejects_removed_kwargs():
    with pytest.raises(TypeError):
        AgentContext(  # type: ignore[call-arg]
            router=MagicMock(),
            user_id="nick",
            project_root=Path("/tmp"),
            db=MagicMock(),
            tool_registry=MagicMock(),
        )


def test_result_and_record_dataclasses_retained():
    rec = ToolCallRecord(tool_name="t", params={}, result={"ok": True}, allowed=True)
    res = AgentResult(status="complete", output={}, tool_calls_made=[rec])
    assert res.tool_calls_made[0].tool_name == "t"
    assert res.status == "complete"
