"""End-to-end pipeline tests for lint_tool_branch (slice 22)."""

from __future__ import annotations

import pytest

from donna.cost.tool_lint import lint_tool_branch

_TOOL_SRC_GOOD = (
    "from typing import Any\n"
    "\n"
    "requires_rebuild = False\n"
    "default_timeout_seconds = 5\n"
    "\n"
    "async def foo(x: Any) -> Any:\n"
    "    return x\n"
)

_TEST_SRC_GOOD = (
    "from donna.skills.tool_test_kit import is_inert_at_import\n"
    "\n"
    "def test_no_io_at_import():\n"
    "    is_inert_at_import('donna.skills.tools.foo')\n"
)


def _diff(*paths: str) -> list[str]:
    return list(paths)


@pytest.mark.asyncio
async def test_clean_branch_passes():
    src = {
        "src/donna/skills/tools/foo.py": _TOOL_SRC_GOOD,
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
        "config/agents.yaml": (
            "agents:\n"
            "  - name: writer\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    result = await lint_tool_branch(
        branch="b/foo",
        diff_paths=_diff(
            "src/donna/skills/tools/foo.py",
            "tests/skills/tools/test_foo.py",
            "config/agents.yaml",
        ),
        tool_name="foo",
        source_text_by_path=src,
    )
    assert result.passed, [f.message for f in result.failures]
    assert result.warnings == []


@pytest.mark.asyncio
async def test_branch_with_anthropic_import_fails():
    src = {
        "src/donna/skills/tools/foo.py": (
            "import anthropic\n"
            "requires_rebuild = False\n"
            "default_timeout_seconds = 5\n"
        ),
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
        "config/agents.yaml": (
            "agents:\n"
            "  - name: writer\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    result = await lint_tool_branch(
        branch="b/foo",
        diff_paths=_diff(
            "src/donna/skills/tools/foo.py",
            "tests/skills/tools/test_foo.py",
            "config/agents.yaml",
        ),
        tool_name="foo",
        source_text_by_path=src,
    )
    assert not result.passed
    assert any(f.rule == "anthropic_import" for f in result.failures)


@pytest.mark.asyncio
async def test_branch_with_secret_fails():
    src = {
        "src/donna/skills/tools/foo.py": (
            'API_KEY = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"\n'
            "requires_rebuild = False\n"
            "default_timeout_seconds = 5\n"
        ),
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
        "config/agents.yaml": (
            "agents:\n"
            "  - name: writer\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    result = await lint_tool_branch(
        branch="b/foo",
        diff_paths=_diff(
            "src/donna/skills/tools/foo.py",
            "tests/skills/tools/test_foo.py",
            "config/agents.yaml",
        ),
        tool_name="foo",
        source_text_by_path=src,
    )
    assert not result.passed
    assert any(f.rule.startswith("secrets:") for f in result.failures)


@pytest.mark.asyncio
async def test_requires_rebuild_true_emits_warning_not_failure():
    src = {
        "src/donna/skills/tools/foo.py": (
            "requires_rebuild = True\n"
            "default_timeout_seconds = 5\n"
        ),
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
        "config/agents.yaml": (
            "agents:\n"
            "  - name: writer\n"
            "    tools:\n"
            "      - foo\n"
        ),
    }
    result = await lint_tool_branch(
        branch="b/foo",
        diff_paths=_diff(
            "src/donna/skills/tools/foo.py",
            "tests/skills/tools/test_foo.py",
            "config/agents.yaml",
        ),
        tool_name="foo",
        source_text_by_path=src,
    )
    assert result.passed
    assert any(w.rule == "requires_rebuild_warning" for w in result.warnings)


@pytest.mark.asyncio
async def test_unallowlisted_marker_passes_without_allowlist_diff():
    src = {
        "src/donna/skills/tools/foo.py": (
            "unallowlisted = True\n"
            "requires_rebuild = False\n"
            "default_timeout_seconds = 5\n"
        ),
        "tests/skills/tools/test_foo.py": _TEST_SRC_GOOD,
    }
    result = await lint_tool_branch(
        branch="b/foo",
        diff_paths=_diff(
            "src/donna/skills/tools/foo.py",
            "tests/skills/tools/test_foo.py",
        ),
        tool_name="foo",
        source_text_by_path=src,
    )
    assert result.passed, [f.message for f in result.failures]
