"""Chat action handlers for vault operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def read_vault_file(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    path = params.get("path", "")
    if not path:
        return ActionResult(success=False, error="File path is required.")

    try:
        if hasattr(ctx.db, "read_vault_file"):
            content = await ctx.db.read_vault_file(path)
        else:
            return ActionResult(success=False, error="Vault read not available via this interface.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to read vault file: {exc}")

    return ActionResult(
        success=True,
        data={"path": path, "content": content},
        summary=f"Read vault file: {path}",
    )


async def create_vault_note(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    title = params.get("title", "")
    content = params.get("content", "")
    folder = params.get("folder", "")

    if not title or not content:
        return ActionResult(success=False, error="Title and content are required.")

    try:
        if hasattr(ctx.db, "create_vault_note"):
            result = await ctx.db.create_vault_note(title=title, content=content, folder=folder)
            return ActionResult(
                success=True,
                data={
                    "title": title,
                    "path": result if isinstance(result, str) else f"{folder}/{title}.md",
                },
                summary=f"Created vault note: {title}",
            )
        return ActionResult(success=False, error="Vault write not available via this interface.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create vault note: {exc}")


async def list_vault_files(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    folder = params.get("folder", "")

    try:
        if hasattr(ctx.db, "list_vault_files"):
            files = await ctx.db.list_vault_files(folder=folder)
        else:
            return ActionResult(
                success=False,
                error="Vault listing not available via this interface.",
            )
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list vault files: {exc}")

    file_list = [{"name": f} if isinstance(f, str) else f for f in files]
    return ActionResult(
        success=True,
        data={"files": file_list, "count": len(file_list), "folder": folder or "/"},
        summary=f"Found {len(file_list)} file(s) in vault{f'/{folder}' if folder else ''}.",
    )
