"""Subprocess import-smoke step (validation, not lint).

Runs ``python -c "import donna.skills.tools.<tool_name>"`` against the
host-repo worktree at the branch tip. Catches import-time errors that
``ast.parse`` cannot see — most commonly ``ImportError`` for missing
optional deps not yet in ``pyproject.toml``.

Implemented as a thin shell-out so we don't have to reload the
orchestrator's own ``sys.modules`` (which would shadow the in-process
ToolRegistry).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()

DEFAULT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class ImportSmokeResult:
    passed: bool
    stdout: str
    stderr: str
    returncode: int


async def run_import_smoke(
    *,
    host_repo_path: Path,
    tool_name: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ImportSmokeResult:
    """Subprocess: ``python -c "import donna.skills.tools.<tool_name>"``.

    Args:
        host_repo_path: Path to the read-only host-repo mount, checked
            out (or worktree-add'd) at the branch.
        tool_name: Tool slug — gets substituted into the import.
        timeout_seconds: Hard wall-clock cap.

    Returns:
        :class:`ImportSmokeResult` (passed=True iff returncode==0).
    """
    cmd = [
        "python",
        "-c",
        f"import donna.skills.tools.{tool_name}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(host_repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning(
            "tool_import_smoke_timeout",
            tool_name=tool_name,
            timeout_seconds=timeout_seconds,
        )
        return ImportSmokeResult(
            passed=False,
            stdout="",
            stderr=f"timed out after {timeout_seconds}s",
            returncode=-1,
        )
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return ImportSmokeResult(
        passed=(proc.returncode == 0),
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode or 0,
    )
