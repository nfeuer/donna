"""Read tool handlers for vault file access.

Provides two handlers for the chat tool-use agent loop:
- list_vault_files: list files in the vault (optionally filtered by folder)
- read_vault_file: read the contents of a specific vault file

Uses ctx.db.list_vault_files and ctx.db.read_vault_file rather than execute_sql.

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()


async def list_vault_files(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """List files in the vault, optionally filtered by folder.

    Args:
        params: Query parameters — folder (optional path prefix).
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with a list of file metadata dicts and total_count.
    """
    folder: str | None = params.get("folder")

    if not hasattr(ctx.db, "list_vault_files"):
        logger.warning("list_vault_files_unavailable")
        return ToolResult(results=[], total_count=0)

    files = await ctx.db.list_vault_files(folder=folder)
    file_list: list[dict[str, Any]] = files if files else []

    logger.debug("list_vault_files", folder=folder, count=len(file_list))

    return ToolResult(results=file_list, total_count=len(file_list))


async def read_vault_file(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Read the contents of a specific vault file.

    Args:
        params: Must contain ``path`` (str) — the vault-relative file path.
        ctx: Tool execution context.

    Returns:
        ToolResult with a single dict containing the file content,
        or empty if the method is unavailable.

    Raises:
        KeyError: If ``path`` is missing from params.
    """
    path: str = params["path"]

    if not hasattr(ctx.db, "read_vault_file"):
        logger.warning("read_vault_file_unavailable", path=path)
        return ToolResult(results=[], total_count=0)

    content = await ctx.db.read_vault_file(path)

    if content is None:
        logger.debug("read_vault_file_not_found", path=path)
        return ToolResult(results=[], total_count=0)

    logger.debug("read_vault_file", path=path)

    return ToolResult(
        results=[{"path": path, "content": content}],
        total_count=1,
    )
