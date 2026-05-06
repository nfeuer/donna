"""§10.5 rows 1 + 6 — required tool metadata.

The tool source file (matched as ``src/donna/skills/tools/<name>.py``)
must declare two module-level assignments:

- ``requires_rebuild = <bool>`` (row 1) — Truthy values are accepted but
  emit a *warning* so the dashboard panel can show the rebuild
  reminder. Slice 24 will hook the hourly Discord nag.
- ``default_timeout_seconds = <int>`` (row 6) — Hard requirement; no
  default.

The check uses :func:`ast.walk` to find module-level
:class:`ast.Assign` nodes. Imported / dynamically-set values are out
of scope.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from donna.cost.tool_lint.types import LintFailure


@dataclass(frozen=True)
class MetadataResult:
    failures: list[LintFailure] = field(default_factory=list)
    warnings: list[LintFailure] = field(default_factory=list)


def _module_level_assigns(tree: ast.Module) -> dict[str, ast.Assign]:
    out: dict[str, ast.Assign] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                out[target.id] = stmt
    return out


def _const(node: ast.AST) -> object | None:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def check_tool_metadata(
    *,
    source_text_by_path: dict[str, str],
    tool_name: str,
) -> MetadataResult:
    """Verify tool source declares the required metadata fields.

    Looks for ``src/donna/skills/tools/<tool_name>.py`` (or
    ``donna/skills/tools/<tool_name>.py`` if the diff is rooted
    differently — picked by suffix match).
    """
    suffix = f"donna/skills/tools/{tool_name}.py"
    target_path: str | None = None
    target_text: str | None = None
    for path, text in source_text_by_path.items():
        if path.endswith(suffix):
            target_path = path
            target_text = text
            break

    if target_text is None or target_path is None:
        return MetadataResult(
            failures=[
                LintFailure(
                    rule="metadata:missing_module",
                    path=suffix,
                    line=None,
                    message=(
                        f"tool source `{suffix}` not present in branch "
                        f"diff — required for tool '{tool_name}'"
                    ),
                )
            ]
        )

    try:
        tree = ast.parse(target_text, filename=target_path)
    except SyntaxError as exc:
        return MetadataResult(
            failures=[
                LintFailure(
                    rule="metadata:syntax",
                    path=target_path,
                    line=exc.lineno,
                    message=str(exc),
                )
            ]
        )

    failures: list[LintFailure] = []
    warnings: list[LintFailure] = []
    assigns = _module_level_assigns(tree)

    rb = assigns.get("requires_rebuild")
    if rb is None:
        failures.append(
            LintFailure(
                rule="metadata:requires_rebuild",
                path=target_path,
                line=None,
                message=(
                    "missing module-level `requires_rebuild = <bool>` — "
                    "required by §10.5 row 1"
                ),
            )
        )
    else:
        rb_value = _const(rb.value)
        if not isinstance(rb_value, bool):
            failures.append(
                LintFailure(
                    rule="metadata:requires_rebuild_type",
                    path=target_path,
                    line=rb.lineno,
                    message=(
                        f"`requires_rebuild` must be a bool literal "
                        f"(got {type(rb_value).__name__ if rb_value is not None else 'None'})"
                    ),
                )
            )
        elif rb_value is True:
            warnings.append(
                LintFailure(
                    rule="requires_rebuild_warning",
                    path=target_path,
                    line=rb.lineno,
                    message=(
                        "`requires_rebuild = True` — registry will refuse "
                        "to mark this tool active until orchestrator "
                        "restart with new build SHA"
                    ),
                )
            )

    to = assigns.get("default_timeout_seconds")
    if to is None:
        failures.append(
            LintFailure(
                rule="metadata:default_timeout",
                path=target_path,
                line=None,
                message=(
                    "missing module-level `default_timeout_seconds = <int>` "
                    "— required by §10.5 row 6"
                ),
            )
        )
    else:
        to_value = _const(to.value)
        if not isinstance(to_value, (int, float)) or isinstance(to_value, bool):
            failures.append(
                LintFailure(
                    rule="metadata:default_timeout_type",
                    path=target_path,
                    line=to.lineno,
                    message=(
                        "`default_timeout_seconds` must be a numeric literal"
                    ),
                )
            )
        elif to_value <= 0:
            failures.append(
                LintFailure(
                    rule="metadata:default_timeout_value",
                    path=target_path,
                    line=to.lineno,
                    message="`default_timeout_seconds` must be positive",
                )
            )

    return MetadataResult(failures=failures, warnings=warnings)
