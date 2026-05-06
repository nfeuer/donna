"""Lint pipeline for ``tool_request_fulfillment`` builds (slice 22).

Realizes docs/superpowers/specs/manual-escalation.md §10.5 — the
extra checks tool builds get on top of the slice-21 ``claude_code``
protocol.

Pipeline (executed in order):

1. AST parse each ``.py`` source under the diff scope.
2. Per-file rules:

   - :func:`donna.cost.tool_lint.anthropic_import.check_anthropic_import`
     (§10.5 row 3)
   - :func:`donna.cost.tool_lint.import_io.check_import_time_io`
     (§10.5 row 5)
   - :func:`donna.cost.tool_lint.secrets.scan_for_secrets`
     (§10.5 row 2)
   - :func:`donna.cost.tool_lint.metadata.check_tool_metadata`
     (§10.5 rows 1 + 6)
3. Whole-diff rules:

   - :func:`donna.cost.tool_lint.allowlist.check_allowlist_update`
     (§10.5 row 4)
   - :func:`donna.cost.tool_lint.inert_test.check_inert_at_import_test`
     (§10.5 row 5)
4. Optional execution gate:

   - :func:`donna.cost.tool_lint.import_smoke.run_import_smoke`
     (validation step — runs after lint passes).

Failures stop validation; warnings (e.g. ``requires_rebuild=True``)
flow through to the dashboard panel.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

import structlog

from donna.cost.tool_lint.allowlist import check_allowlist_update
from donna.cost.tool_lint.anthropic_import import check_anthropic_import
from donna.cost.tool_lint.import_io import check_import_time_io
from donna.cost.tool_lint.inert_test import check_inert_at_import_test
from donna.cost.tool_lint.metadata import check_tool_metadata
from donna.cost.tool_lint.secrets import scan_for_secrets
from donna.cost.tool_lint.types import LintFailure

logger = structlog.get_logger()


@dataclass(frozen=True)
class LintResult:
    failures: list[LintFailure] = field(default_factory=list)
    warnings: list[LintFailure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class ToolLintConfig:
    detect_secrets_enabled: bool = False
    requires_rebuild_default: bool = False
    default_timeout_seconds: int = 5


async def lint_tool_branch(
    *,
    branch: str,
    diff_paths: list[str],
    tool_name: str,
    source_text_by_path: dict[str, str],
    config: ToolLintConfig | None = None,
) -> LintResult:
    """Run every §10.5 lint rule against a tool-build branch.

    Args:
        branch: Branch name (used only for logging).
        diff_paths: Paths the user touched, scope-validated by
            :class:`donna.cost.diff_validator.DiffValidator`.
        tool_name: The tool being built (matches ``{name}``
            substitution from task_types.yaml ``target_paths``).
        source_text_by_path: Pre-fetched committed source for every
            path in ``diff_paths`` (caller uses ``host_repo.show_file``).
        config: Tunables (``detect-secrets`` opt-in flag, defaults).

    Returns:
        :class:`LintResult` with per-rule failures + warnings.
    """
    cfg = config or ToolLintConfig()
    failures: list[LintFailure] = []
    warnings: list[LintFailure] = []

    for path, text in source_text_by_path.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            failures.append(
                LintFailure(
                    rule="syntax",
                    path=path,
                    line=exc.lineno,
                    message=f"could not parse {path}: {exc.msg}",
                )
            )
            continue
        failures.extend(check_anthropic_import(tree, path))
        failures.extend(check_import_time_io(tree, path))
        secret_failures = scan_for_secrets(
            text, path, detect_secrets_enabled=cfg.detect_secrets_enabled
        )
        failures.extend(secret_failures)

    # Tool metadata + dependent diff-wide rules
    metadata_results = check_tool_metadata(
        source_text_by_path=source_text_by_path,
        tool_name=tool_name,
    )
    failures.extend(metadata_results.failures)
    warnings.extend(metadata_results.warnings)

    failures.extend(check_allowlist_update(diff_paths, source_text_by_path, tool_name))
    failures.extend(
        check_inert_at_import_test(diff_paths, source_text_by_path, tool_name)
    )

    logger.info(
        "tool_lint_completed",
        branch=branch,
        tool_name=tool_name,
        failure_count=len(failures),
        warning_count=len(warnings),
    )
    return LintResult(failures=failures, warnings=warnings)


__all__ = [
    "LintFailure",
    "LintResult",
    "ToolLintConfig",
    "check_allowlist_update",
    "check_anthropic_import",
    "check_import_time_io",
    "check_inert_at_import_test",
    "check_tool_metadata",
    "lint_tool_branch",
    "scan_for_secrets",
]


# Re-export for callers that use ``from donna.cost.tool_lint import ...``.
_ = Any
