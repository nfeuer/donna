"""Lint tests — anthropic_import (slice 22 §10.5 row 3)."""

from __future__ import annotations

import ast

from donna.cost.tool_lint.anthropic_import import check_anthropic_import


def _tree(src: str) -> ast.AST:
    return ast.parse(src)


def test_rejects_top_level_import_anthropic_in_tool():
    failures = check_anthropic_import(
        _tree("import anthropic\n"), "src/donna/skills/tools/x.py"
    )
    assert any(f.rule == "anthropic_import" for f in failures)


def test_rejects_from_anthropic_in_tool():
    failures = check_anthropic_import(
        _tree("from anthropic.types import Message\n"),
        "src/donna/skills/tools/x.py",
    )
    assert len(failures) == 1


def test_allows_import_anthropic_inside_llm_dir():
    failures = check_anthropic_import(
        _tree("import anthropic\n"), "src/donna/llm/anthropic_client.py"
    )
    assert failures == []


def test_passes_clean_module():
    failures = check_anthropic_import(
        _tree("import os\n"), "src/donna/skills/tools/x.py"
    )
    assert failures == []


def test_rejects_anthropic_dotted_import():
    failures = check_anthropic_import(
        _tree("import anthropic.types\n"), "src/donna/skills/tools/x.py"
    )
    assert len(failures) == 1
