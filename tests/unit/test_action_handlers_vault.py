"""Tests for vault action handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.actions.vault import create_vault_note, list_vault_files, read_vault_file
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


@pytest.mark.asyncio
async def test_read_vault_file_missing_path(ctx: ActionContext) -> None:
    result = await read_vault_file({}, ctx)
    assert result.success is False
    assert "path is required" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_read_vault_file_success(ctx: ActionContext) -> None:
    ctx.db.read_vault_file = AsyncMock(return_value="# Hello\nWorld")
    result = await read_vault_file({"path": "notes/hello.md"}, ctx)
    assert result.success is True
    assert result.data["content"] == "# Hello\nWorld"


@pytest.mark.asyncio
async def test_create_vault_note_missing_fields(ctx: ActionContext) -> None:
    result = await create_vault_note({"title": "Test"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_vault_note_success(ctx: ActionContext) -> None:
    ctx.db.create_vault_note = AsyncMock(return_value="notes/test.md")
    result = await create_vault_note({"title": "Test", "content": "Body"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_list_vault_files_success(ctx: ActionContext) -> None:
    ctx.db.list_vault_files = AsyncMock(return_value=["a.md", "b.md"])
    result = await list_vault_files({}, ctx)
    assert result.success is True
    assert result.data["count"] == 2
