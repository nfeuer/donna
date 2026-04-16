"""Flow control DSL — for_each primitive for skill tool invocations.

v1 scope: only for_each. retry is handled by ToolDispatcher.
escalate is handled by SkillExecutor via an output-schema field.
"""

from __future__ import annotations

from typing import Any

import jinja2

from donna.skills._render import render_value
from donna.skills.tool_dispatch import ToolInvocationSpec


class DSLError(Exception):
    pass


def expand_for_each(
    block: dict[str, Any],
    state: dict,
    inputs: dict,
) -> list[ToolInvocationSpec]:
    """Expand a for_each block into concrete ToolInvocationSpec list."""
    iterable_expr = block.get("for_each")
    as_var = block.get("as")
    tool = block.get("tool")

    if not iterable_expr or not as_var or not tool:
        raise DSLError("for_each requires 'for_each', 'as', and 'tool' fields")

    try:
        resolved = render_value(
            iterable_expr,
            context={"state": state, "inputs": inputs},
            preserve_types=True,
        )
    except jinja2.UndefinedError as exc:
        raise DSLError(f"expression eval failed: {exc}") from exc

    if not isinstance(resolved, list):
        raise DSLError(
            f"for_each expression must be a list, got {type(resolved).__name__}"
        )

    specs: list[ToolInvocationSpec] = []
    for index, item in enumerate(resolved):
        loop_ctx = {"index0": index, "index": index + 1, "length": len(resolved)}
        context = {as_var: item, "loop": loop_ctx, "state": state, "inputs": inputs}

        try:
            rendered_args = render_value(
                block.get("args", {}),
                context=context,
                preserve_types=True,
            )
            rendered_store = render_value(
                block.get("store_as", "result"),
                context=context,
                preserve_types=True,
            )
        except jinja2.UndefinedError as exc:
            raise DSLError(f"template render failed: {exc}") from exc

        specs.append(ToolInvocationSpec(
            tool=tool,
            args=rendered_args,
            store_as=rendered_store,
            retry=block.get("retry", {}),
        ))

    return specs
