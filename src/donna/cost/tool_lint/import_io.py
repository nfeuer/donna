"""§10.5 row 5 — module-level I/O is forbidden in tool source.

Tools must be inert at import time so :class:`donna.skills.validation_executor.ValidationExecutor`
can register them without firing real network/disk side effects. This
matches the spec's ``is_inert_at_import`` test fixture (also enforced
separately by :mod:`donna.cost.tool_lint.inert_test`).

The check walks only the **module body** (top level) — function and
class bodies are intentionally not descended, since deferred I/O is
fine. Module-level ``If`` / ``Try`` blocks (e.g. for import-guarded
fallbacks) are descended one level, then stop.

Heuristic — flag a top-level :class:`ast.Call` whose target chain
starts with any of:

- ``open``, ``socket``, ``subprocess``
- ``requests``, ``urllib``, ``aiohttp``, ``httpx``
- ``os.system``, ``os.popen``
- ``asyncio.run``
- ``pathlib.Path(...).read_text/write_text/read_bytes/write_bytes``

False positives on intentional sentinel constants are accepted; the
right escape hatch is to wrap the call in a function and call it from
``__init__``-style hooks rather than at module scope.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from donna.cost.tool_lint.types import LintFailure

# Names whose attribute access at module level should fail the check.
FORBIDDEN_ROOTS: frozenset[str] = frozenset(
    {
        "open",
        "requests",
        "urllib",
        "aiohttp",
        "httpx",
        "socket",
        "subprocess",
    }
)

# Specific dotted attribute roots that should fail.
FORBIDDEN_DOTTED: frozenset[tuple[str, str]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("asyncio", "run"),
    }
)

# pathlib pattern: Path(...).<method> at module level.
PATHLIB_BAD_METHODS: frozenset[str] = frozenset(
    {"read_text", "write_text", "read_bytes", "write_bytes", "open"}
)


def _root_chain(call: ast.Call) -> tuple[str, ...]:
    """Return the dotted name chain of a Call's func, e.g. ``("os","system")``.

    Returns empty tuple if the chain isn't a pure attribute chain.
    """
    parts: list[str] = []
    node: ast.AST = call.func
    while True:
        if isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            parts.insert(0, node.id)
            return tuple(parts)
        else:
            return ()


def _flag_call(call: ast.Call) -> str | None:
    chain = _root_chain(call)
    if not chain:
        # pathlib: Path(...).read_text() pattern — Call(func=Attribute(...))
        if (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in PATHLIB_BAD_METHODS
        ):
            inner = call.func.value
            if isinstance(inner, ast.Call):
                inner_chain = _root_chain(inner)
                # Path(...) call?
                if inner_chain and inner_chain[-1] == "Path":
                    return f"pathlib.Path(...).{call.func.attr}() at import time"
        return None

    if chain[0] in FORBIDDEN_ROOTS:
        # Single-name builtin like open(...) or attribute chain like
        # requests.get(...). Allow `open` *only* if the AST walker enters
        # a function — but we already filter by parent context.
        return ".".join(chain) + "(...)"
    if len(chain) >= 2 and (chain[0], chain[1]) in FORBIDDEN_DOTTED:
        return ".".join(chain) + "(...)"
    return None


def _module_body_calls(tree: ast.AST) -> Iterable[ast.Call]:
    """Yield Calls executed at module load time.

    Descends into module-level ``If`` and ``Try`` (one level deep) so
    we still catch ``if condition: requests.get(...)`` patterns. Stops
    at function and class boundaries.
    """
    if not isinstance(tree, ast.Module):
        return
    for node in tree.body:
        yield from _walk_top_level(node)


def _walk_top_level(node: ast.AST) -> Iterable[ast.Call]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    if isinstance(node, ast.Expr):
        if isinstance(node.value, ast.Call):
            yield node.value
        return
    if isinstance(node, ast.Assign):
        for value in ast.walk(node.value):
            if isinstance(value, ast.Call):
                yield value
        return
    if isinstance(node, (ast.If, ast.Try, ast.With)):
        children: list[ast.stmt] = []
        if isinstance(node, ast.If):
            children = list(node.body) + list(node.orelse)
        elif isinstance(node, ast.Try):
            children = (
                list(node.body)
                + list(node.orelse)
                + [
                    stmt
                    for handler in node.handlers
                    for stmt in handler.body
                ]
                + list(node.finalbody)
            )
        elif isinstance(node, ast.With):
            children = list(node.body)
        for child in children:
            yield from _walk_top_level(child)
        return
    # Other statement types: walk Calls embedded in their direct expressions.
    for descendant in ast.iter_child_nodes(node):
        if isinstance(descendant, ast.Call):
            yield descendant


def check_import_time_io(tree: ast.AST, path: str) -> list[LintFailure]:
    """Reject top-level network/disk I/O in tool source files."""
    failures: list[LintFailure] = []
    for call in _module_body_calls(tree):
        flagged = _flag_call(call)
        if flagged is None:
            continue
        failures.append(
            LintFailure(
                rule="import_io",
                path=path,
                line=call.lineno,
                message=(
                    f"module-level I/O `{flagged}` — wrap in a function "
                    "and call from a deferred entrypoint"
                ),
            )
        )
    return failures
