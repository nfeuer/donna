"""Lint tests — tool metadata (slice 22 §10.5 rows 1 + 6)."""

from __future__ import annotations

from donna.cost.tool_lint.metadata import check_tool_metadata


_GOOD = (
    "requires_rebuild = False\n"
    "default_timeout_seconds = 5\n"
    "async def tool(x):\n    return x\n"
)


def test_passes_with_required_metadata():
    src = {"src/donna/skills/tools/foo.py": _GOOD}
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert result.failures == []


def test_warns_when_requires_rebuild_true():
    src = {
        "src/donna/skills/tools/foo.py": (
            "requires_rebuild = True\n"
            "default_timeout_seconds = 10\n"
        ),
    }
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert result.failures == []
    assert any(f.rule == "requires_rebuild_warning" for f in result.warnings)


def test_fails_missing_requires_rebuild():
    src = {"src/donna/skills/tools/foo.py": "default_timeout_seconds = 5\n"}
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert any(f.rule == "metadata:requires_rebuild" for f in result.failures)


def test_fails_missing_default_timeout():
    src = {"src/donna/skills/tools/foo.py": "requires_rebuild = False\n"}
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert any(f.rule == "metadata:default_timeout" for f in result.failures)


def test_fails_when_module_missing():
    src = {"some/other/path.py": _GOOD}
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert any(f.rule == "metadata:missing_module" for f in result.failures)


def test_fails_non_bool_requires_rebuild():
    src = {
        "src/donna/skills/tools/foo.py": (
            "requires_rebuild = 'yes'\n"
            "default_timeout_seconds = 5\n"
        ),
    }
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert any(f.rule == "metadata:requires_rebuild_type" for f in result.failures)


def test_fails_non_positive_timeout():
    src = {
        "src/donna/skills/tools/foo.py": (
            "requires_rebuild = False\n"
            "default_timeout_seconds = 0\n"
        ),
    }
    result = check_tool_metadata(source_text_by_path=src, tool_name="foo")
    assert any(f.rule == "metadata:default_timeout_value" for f in result.failures)
