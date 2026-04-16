"""Flow control DSL — for_each primitive for skill tool invocations.

v1 scope: only for_each. retry is handled by ToolDispatcher.
escalate is handled by SkillExecutor via an output-schema field.
"""

from __future__ import annotations

import re
from typing import Any

import jinja2

from donna.skills.tool_dispatch import ToolInvocationSpec


class DSLError(Exception):
    pass


_jinja = jinja2.Environment(
    autoescape=False,
    undefined=jinja2.StrictUndefined,
)

# Whole-value single-expression detector. When the value is exactly
# "{{ <expr> }}" (whitespace allowed), we evaluate it natively to preserve
# Python types (lists stay lists, numbers stay numbers).
_WHOLE_EXPR_RE = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")


class _AttrDict:
    """Wrapper that makes dict keys accessible as attributes.

    Jinja's compile_expression resolves ``foo.bar`` via ``getattr(foo, 'bar')``.
    For plain dicts this hits built-in methods (e.g. ``dict.items``) before
    reaching the key. This wrapper prefers key lookup over real attributes so
    that ``inputs.items`` resolves to ``inputs["items"]`` as intended.
    """

    def __init__(self, d: dict) -> None:
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name: str) -> Any:
        d = object.__getattribute__(self, "_d")
        try:
            val = d[name]
        except KeyError:
            raise AttributeError(name) from None
        # Recursively wrap nested dicts so dotted paths keep working.
        return _AttrDict(val) if isinstance(val, dict) else val

    def __iter__(self):  # needed if Jinja iterates the value
        return iter(object.__getattribute__(self, "_d"))

    def __repr__(self) -> str:  # pragma: no cover
        return repr(object.__getattribute__(self, "_d"))


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

    resolved = _render_value(iterable_expr, state=state, inputs=inputs, extra={})
    if not isinstance(resolved, list):
        raise DSLError(
            f"for_each expression must be a list, got {type(resolved).__name__}"
        )

    specs: list[ToolInvocationSpec] = []
    for index, item in enumerate(resolved):
        loop_ctx = {"index0": index, "index": index + 1, "length": len(resolved)}
        extra_ctx = {as_var: item, "loop": loop_ctx}

        rendered_args = _render_args(
            block.get("args", {}), state=state, inputs=inputs, extra=extra_ctx,
        )
        rendered_store = _render_value(
            block.get("store_as", "result"), state=state, inputs=inputs, extra=extra_ctx,
        )

        specs.append(ToolInvocationSpec(
            tool=tool,
            args=rendered_args,
            store_as=rendered_store,
            retry=block.get("retry", {}),
        ))

    return specs


def _render_value(value: Any, state: dict, inputs: dict, extra: dict) -> Any:
    if isinstance(value, str):
        m = _WHOLE_EXPR_RE.match(value)
        if m:
            # Whole value is a single {{ expr }} — try native evaluation first
            # to preserve collection types (lists, dicts). Wrap state/inputs
            # so dotted key access (e.g. inputs.items) resolves dict keys
            # rather than built-in dict methods.
            try:
                expr = _jinja.compile_expression(m.group(1))
                wrapped_extra = {
                    k: _AttrDict(v) if isinstance(v, dict) else v
                    for k, v in extra.items()
                }
                result = expr(
                    state=_AttrDict(state),
                    inputs=_AttrDict(inputs),
                    **wrapped_extra,
                )
                # Preserve type only for collections; scalars fall through to
                # string rendering so "{{ loop.index0 }}" → "0" not 0.
                if isinstance(result, (list, dict)):
                    return result
            except jinja2.UndefinedError as exc:
                raise DSLError(f"expression eval failed: {exc}") from exc
        # Standard string render (also the scalar fallback from native eval).
        try:
            return _jinja.from_string(value).render(state=state, inputs=inputs, **extra)
        except jinja2.UndefinedError as exc:
            raise DSLError(f"template render failed: {exc}") from exc
    if isinstance(value, dict):
        return _render_args(value, state=state, inputs=inputs, extra=extra)
    if isinstance(value, list):
        return [_render_value(v, state=state, inputs=inputs, extra=extra) for v in value]
    return value


def _render_args(args: dict, state: dict, inputs: dict, extra: dict) -> dict:
    return {k: _render_value(v, state=state, inputs=inputs, extra=extra) for k, v in args.items()}
