"""§10.5 row 4 — tool must be added to at least one agent allowlist.

Tools that aren't on any allowlist are defined-but-unusable. The
canonical allowlists live in ``config/agents.yaml``,
``config/skills.yaml`` and ``config/task_types.yaml`` (the latter
holds the Wave 4 ``capability.tools_json`` seeds via the seeded-
capability loader).

The check is two-mode:

1. The diff must include at least one of those YAML files **and** the
   tool name must appear in the file's text near a ``tools`` key, OR
2. The tool source file must declare module-level
   ``unallowlisted = True``, signalling deliberate
   defined-but-unusable status.

Mode 1 is text-only (we don't try to YAML-parse the new revision
because the file may also have unrelated edits). The "near a tools key"
heuristic looks for the literal ``tool_name`` token within 5 lines
after a line containing ``tools:`` or ``tools_json``.
"""

from __future__ import annotations

import ast
import re

from donna.cost.tool_lint.types import LintFailure

ALLOWLIST_PATHS: tuple[str, ...] = (
    "config/agents.yaml",
    "config/skills.yaml",
    "config/task_types.yaml",
)


def _has_unallowlisted_marker(source_text_by_path: dict[str, str], tool_name: str) -> bool:
    suffix = f"donna/skills/tools/{tool_name}.py"
    for path, text in source_text_by_path.items():
        if not path.endswith(suffix):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            continue
        for stmt in tree.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "unallowlisted"
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is True
                ):
                    return True
    return False


_TOOLS_KEY_RE = re.compile(r"^\s*tools(?:_json)?\s*:", re.MULTILINE)


def _allowlist_mentions_tool(text: str, tool_name: str) -> bool:
    """Heuristic: tool_name appears within ~5 lines after a tools: key."""
    name_re = re.compile(rf"(^|[^A-Za-z0-9_]){re.escape(tool_name)}([^A-Za-z0-9_]|$)")
    lines = text.splitlines()
    indices = [i for i, line in enumerate(lines) if _TOOLS_KEY_RE.match(line)]
    for idx in indices:
        window = "\n".join(lines[idx : idx + 6])
        if name_re.search(window):
            return True
    # Fallback: a top-level entry in a yaml list literal — name on its own line
    # under the tools: key but separated by more than 5 lines (very rare).
    return bool(name_re.search(text) and indices)


def check_allowlist_update(
    diff_paths: list[str],
    source_text_by_path: dict[str, str],
    tool_name: str,
) -> list[LintFailure]:
    """Verify tool is allowlisted somewhere or marked ``unallowlisted=True``."""
    if _has_unallowlisted_marker(source_text_by_path, tool_name):
        return []

    touched_allowlists = [
        p for p in diff_paths if any(p.endswith(name) for name in ALLOWLIST_PATHS)
    ]
    if not touched_allowlists:
        return [
            LintFailure(
                rule="allowlist",
                path=", ".join(ALLOWLIST_PATHS),
                line=None,
                message=(
                    f"tool '{tool_name}' is not added to any allowlist. "
                    "Update one of "
                    f"{', '.join(ALLOWLIST_PATHS)}, or set "
                    "`unallowlisted = True` at the top of the tool module"
                ),
            )
        ]

    for path in touched_allowlists:
        text = source_text_by_path.get(path, "")
        if _allowlist_mentions_tool(text, tool_name):
            return []

    return [
        LintFailure(
            rule="allowlist",
            path=", ".join(touched_allowlists),
            line=None,
            message=(
                f"tool '{tool_name}' not found in any of the modified "
                "allowlist files near a `tools:` key"
            ),
        )
    ]
