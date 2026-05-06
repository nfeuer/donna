"""§10.5 row 3 — block ``import anthropic`` outside ``src/donna/llm/``.

All Anthropic API calls must route through the
:mod:`donna.llm` gateway so cost, model routing, and structured-output
guarantees apply uniformly. A tool that imports ``anthropic`` directly
bypasses every cost-tracking guarantee and is hard-failed at validation.

The check is AST-based — it covers ``import anthropic``,
``from anthropic import Foo``, and ``from anthropic.types import …``.
Dynamic forms (``__import__("anthropic")``) are out of scope; slice 24
may extend the rule.
"""

from __future__ import annotations

import ast

from donna.cost.tool_lint.types import LintFailure

ALLOWED_PATH_PREFIX = "src/donna/llm/"


def _is_allowed(path: str) -> bool:
    return path.startswith(ALLOWED_PATH_PREFIX)


def check_anthropic_import(tree: ast.AST, path: str) -> list[LintFailure]:
    """Walk ``tree`` and reject any ``anthropic[.…]`` import.

    Returns one :class:`LintFailure` per offending statement.
    """
    if _is_allowed(path):
        return []

    failures: list[LintFailure] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                    failures.append(
                        LintFailure(
                            rule="anthropic_import",
                            path=path,
                            line=node.lineno,
                            message=(
                                f"`import {alias.name}` outside "
                                f"src/donna/llm/ — route through donna.llm "
                                "gateway"
                            ),
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "anthropic" or module.startswith("anthropic."):
                failures.append(
                    LintFailure(
                        rule="anthropic_import",
                        path=path,
                        line=node.lineno,
                        message=(
                            f"`from {module} import …` outside "
                            f"src/donna/llm/ — route through donna.llm "
                            "gateway"
                        ),
                    )
                )
    return failures
