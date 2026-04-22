"""Shared Jinja rendering helper for skill DSL and tool dispatcher.

Consolidates the two prior copies in dsl.py and tool_dispatch.py.
"""

from __future__ import annotations

import re
from typing import Any

import jinja2

_JINJA = jinja2.Environment(
    autoescape=False,
    undefined=jinja2.StrictUndefined,
)

_WHOLE_EXPR_RE = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")


class _AttrDict:
    """Wrapper that makes dict keys accessible as attributes.

    Jinja's attribute lookup (``foo.bar``) resolves via getattr first. For plain
    dicts this hits built-in methods (e.g. ``dict.items``) before the key.
    This wrapper prefers key lookup over real attributes so that
    ``inputs.items`` resolves to ``inputs["items"]`` as intended.
    """

    def __init__(self, d: dict[str, Any]) -> None:
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


def _wrap_context(context: dict[str, Any]) -> dict[str, Any]:
    """Wrap every dict value in *context* with :class:`_AttrDict`."""
    return {k: (_AttrDict(v) if isinstance(v, dict) else v) for k, v in context.items()}


def render_value(
    value: Any,
    context: dict[str, Any],
    preserve_types: bool = True,
) -> Any:
    """Render a potentially nested value using Jinja templates.

    Args:
        value: The value to render. Can be a string, dict, list, or scalar.
        context: Mapping passed as kwargs to the Jinja environment. Dict values
            are wrapped with :class:`_AttrDict` so dotted key access works
            consistently (e.g. ``state.foo`` resolves ``state["foo"]``).
        preserve_types: When ``True``, whole-value expressions (``{{ expr }}``)
            that evaluate to a list or dict are returned as-is (not stringified).
            When ``False``, always performs string interpolation — matches the
            original :mod:`tool_dispatch` behaviour exactly.

    Raises:
        jinja2.UndefinedError: If the template references an undefined variable.
    """
    if isinstance(value, str):
        wrapped = _wrap_context(context)
        if preserve_types:
            m = _WHOLE_EXPR_RE.match(value)
            if m:
                expr = _JINJA.compile_expression(m.group(1))
                result = expr(**wrapped)
                # _AttrDict wraps dicts during attribute traversal; unwrap back
                # to a plain dict so callers receive the expected type.
                if isinstance(result, _AttrDict):
                    result = object.__getattribute__(result, "_d")
                # Preserve type only for collections; scalars fall through to
                # string rendering so ``{{ loop.index0 }}`` → ``"0"`` not ``0``.
                if isinstance(result, (list, dict)):
                    return result
        return _JINJA.from_string(value).render(**wrapped)
    if isinstance(value, dict):
        return {
            k: render_value(v, context, preserve_types=preserve_types)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [render_value(v, context, preserve_types=preserve_types) for v in value]
    return value
