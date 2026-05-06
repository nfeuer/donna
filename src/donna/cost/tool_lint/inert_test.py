"""§10.5 row 5 — branch must include an `is_inert_at_import` test.

Tools must come with a regression test asserting they have **no** I/O
side effects at module import time. The convention is:

- File: ``tests/skills/tools/test_<tool_name>.py``
- Body: at least one ``Call`` to
  :func:`donna.skills.tool_test_kit.is_inert_at_import` with the
  literal target ``"donna.skills.tools.<tool_name>"``.

The check uses :func:`ast.walk` over the test file. The
``is_inert_at_import`` helper itself lives in
:mod:`donna.skills.tool_test_kit` so tool branches just import it.
"""

from __future__ import annotations

import ast

from donna.cost.tool_lint.types import LintFailure


def _expected_test_path(tool_name: str) -> str:
    return f"tests/skills/tools/test_{tool_name}.py"


def _expected_module_arg(tool_name: str) -> str:
    return f"donna.skills.tools.{tool_name}"


def _has_inert_call(tree: ast.AST, expected_arg: str) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``is_inert_at_import("…")`` — both bare and dotted forms.
        called: str | None = None
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called != "is_inert_at_import":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == expected_arg:
            return True
    return False


def check_inert_at_import_test(
    diff_paths: list[str],
    source_text_by_path: dict[str, str],
    tool_name: str,
) -> list[LintFailure]:
    expected_path = _expected_test_path(tool_name)
    expected_arg = _expected_module_arg(tool_name)

    matched_path: str | None = None
    for path in diff_paths:
        if path.endswith(expected_path):
            matched_path = path
            break
    if matched_path is None:
        return [
            LintFailure(
                rule="inert_test",
                path=expected_path,
                line=None,
                message=(
                    f"missing required test file `{expected_path}` — "
                    "must call `is_inert_at_import('"
                    f"{expected_arg}')` (§10.5 row 5)"
                ),
            )
        ]

    text = source_text_by_path.get(matched_path, "")
    try:
        tree = ast.parse(text, filename=matched_path)
    except SyntaxError as exc:
        return [
            LintFailure(
                rule="inert_test:syntax",
                path=matched_path,
                line=exc.lineno,
                message=str(exc),
            )
        ]

    if not _has_inert_call(tree, expected_arg):
        return [
            LintFailure(
                rule="inert_test",
                path=matched_path,
                line=None,
                message=(
                    f"`{matched_path}` does not call "
                    f"`is_inert_at_import('{expected_arg}')`"
                ),
            )
        ]
    return []
