"""Render the manual claude_code spec template + write to disk (slice 21).

Donna writes one ``${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md``
file per claude_code escalation. The file is the canonical "what to
build" artifact the user pastes into Claude Code; the dashboard renders
the same content from ``escalation_request.prompt_body`` (mirror).

Realizes docs/superpowers/specs/manual-escalation.md §5.3 (claude_code
mode protocol) and §9 (prompt template paths).

The builder is a pure function plus one filesystem write — no DB
access, no config side effects. The caller (gate) feeds it the
already-resolved per-task-type ``manual_escalation`` block, the
originating-entity name (substituted into ``{name}`` placeholders),
and bookkeeping fields (correlation_id, branch_name, base_sha).
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from donna.config import ManualEscalationTaskTypeConfig

logger = structlog.get_logger()


# Substitution token allowed in target_paths globs / reference_module.
# Single-char alphabet (lowercase letter, digit, underscore) is enough
# for the names we generate from ``capability_name`` / ``skill.name``.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclasses.dataclass(frozen=True)
class RenderedSpec:
    """Output of :meth:`ClaudeCodeSpecBuilder.render`."""

    body: str
    """The rendered markdown spec."""

    path: Path
    """Absolute path the spec was written to."""

    branch_name: str
    """Sanitized branch name embedded in the spec."""

    target_paths: dict[str, str]
    """Glob dict with ``{name}`` substituted, mirrored onto the row."""

    worktree_command: str
    """Pre-rendered ``git worktree add`` line for dashboard copy-on-click."""


class ClaudeCodeSpecBuilder:
    """Renders claude_code spec markdown into the workspace.

    Construct once at startup with the resolved paths; call
    :meth:`render` per escalation. Thread-safe (Jinja env is reusable).
    """

    def __init__(
        self,
        *,
        prompt_dir: Path,
        workspace_path: Path,
        host_repo_path: Path,
        worktree_root: Path,
        dashboard_base_url: str,
        iteration_limit: int = 3,
    ) -> None:
        self._workspace_path = Path(workspace_path)
        self._host_repo_path = Path(host_repo_path)
        self._worktree_root = Path(worktree_root)
        self._dashboard_base_url = dashboard_base_url.rstrip("/")
        self._iteration_limit = iteration_limit
        self._env = Environment(
            loader=FileSystemLoader(str(prompt_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        *,
        correlation_id: str,
        task_type: str,
        capability_name: str,
        manual: ManualEscalationTaskTypeConfig,
        base_sha: str,
        task_summary: str,
        acceptance_criteria: list[str],
        template: str = "skill_draft.md",
    ) -> RenderedSpec:
        """Render the spec, write to disk, return the artifact.

        Args:
            correlation_id: UUIDv7 from the gate; used in branch name and
                spec filename.
            task_type: e.g. ``skill_auto_draft`` / ``skill_evolution``.
            capability_name: Substituted into ``{name}`` placeholders. Must
                match ``[a-z][a-z0-9_]*`` to avoid path-traversal.
            manual: The per-task-type ``manual_escalation`` block.
            base_sha: Pinned ``main`` SHA captured at gate-fire time.
            task_summary: Free-text headline (typically built by the gate).
            acceptance_criteria: Bullet points for what "done" means.
            template: Override for the Jinja file (lets slice 22 swap in
                ``tool_build.md`` without forking this class).

        Raises:
            ValueError: capability_name fails the safe-name check.
            jinja2.exceptions.TemplateError: template render error.
            OSError: write to disk failed.
        """
        if not _NAME_RE.match(capability_name):
            raise ValueError(
                f"capability_name {capability_name!r} must match {_NAME_RE.pattern!r} — "
                "claude_code spec will not be rendered."
            )

        target_paths = {
            label: glob.format(name=capability_name)
            for label, glob in (manual.target_paths or {}).items()
        }
        reference_module_path = (
            manual.reference_module.format(name=capability_name)
            if manual.reference_module
            else "(no reference module configured)"
        )

        branch_name = _branch_name(correlation_id, capability_name)
        worktree_path = self._worktree_root / correlation_id
        worktree_command = (
            f"git worktree add -b {branch_name} "
            f'"{worktree_path}" {base_sha}'
        )

        target_paths_for_add = " ".join(
            f'"{p}"' for p in target_paths.values()
        )

        ctx: dict[str, Any] = {
            "correlation_id": correlation_id,
            "task_type": task_type,
            "capability_name": capability_name,
            "branch_name": branch_name,
            "base_sha": base_sha,
            "task_summary": task_summary,
            "acceptance_criteria": list(acceptance_criteria),
            "target_paths": target_paths,
            "target_paths_for_add": target_paths_for_add,
            "reference_module_path": reference_module_path,
            "forbidden_patterns": list(manual.forbidden_patterns),
            "host_repo_path": str(self._host_repo_path),
            "worktree_path": str(worktree_path),
            "worktree_command": worktree_command,
            "dashboard_url": (
                f"{self._dashboard_base_url}/escalations/{correlation_id}"
            ),
            "iteration_limit": self._iteration_limit,
        }
        body = self._env.get_template(template).render(**ctx)

        spec_dir = self._workspace_path / "escalations"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_path = spec_dir / f"{correlation_id}.md"
        spec_path.write_text(body, encoding="utf-8")

        logger.info(
            "claude_code_spec_rendered",
            correlation_id=correlation_id,
            spec_path=str(spec_path),
            capability_name=capability_name,
            branch_name=branch_name,
            base_sha=base_sha,
        )
        return RenderedSpec(
            body=body,
            path=spec_path,
            branch_name=branch_name,
            target_paths=target_paths,
            worktree_command=worktree_command,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _branch_name(correlation_id: str, capability_name: str) -> str:
    """Generate a deterministic branch name for the worktree.

    Format: ``escalation/<short-corr>-<capability>``. Short-corr is the
    first 8 chars of the UUIDv7 — collision-free across the lifetime of
    the homelab and human-readable.
    """
    short = correlation_id.replace("-", "")[:8]
    return f"escalation/{short}-{capability_name}"


def expand_workspace_path(template: str) -> Path:
    """Expand ``${DONNA_WORKSPACE_PATH}`` and ``~`` in a path template."""
    return Path(os.path.expandvars(os.path.expanduser(template)))
