"""Unit tests for the admin config and prompt endpoints.

Uses tmp_path for real file I/O — no DB mocking needed for most tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_config import (
    get_config,
    get_prompt,
    list_configs,
    list_prompts,
    put_config,
    put_prompt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(config_dir: Path, project_root: Path | None = None) -> MagicMock:
    """Build a mock request with config_dir set."""
    request = MagicMock()
    request.app.state.config_dir = str(config_dir)
    if project_root:
        import os
        # Set DONNA_PROJECT_ROOT so _get_project_root uses it
        os.environ["DONNA_PROJECT_ROOT"] = str(project_root)
    return request


# ---------------------------------------------------------------------------
# list_configs
# ---------------------------------------------------------------------------


class TestListConfigs:
    async def test_lists_existing_files(self, tmp_path: Path) -> None:
        (tmp_path / "agents.yaml").write_text("agents: {}")
        (tmp_path / "task_types.yaml").write_text("task_types: {}")
        request = _make_request(tmp_path)
        result = await list_configs(request)
        names = [f["name"] for f in result["configs"]]
        assert "agents.yaml" in names
        assert "task_types.yaml" in names

    async def test_excludes_nonexistent_files(self, tmp_path: Path) -> None:
        # Only create one allowed file
        (tmp_path / "agents.yaml").write_text("agents: {}")
        request = _make_request(tmp_path)
        result = await list_configs(request)
        names = [f["name"] for f in result["configs"]]
        assert "agents.yaml" in names
        assert "email.yaml" not in names

    async def test_empty_config_dir(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path)
        result = await list_configs(request)
        assert result["configs"] == []


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------


class TestGetConfig:
    async def test_reads_allowed_file(self, tmp_path: Path) -> None:
        content = "agents:\n  pm:\n    enabled: true\n"
        (tmp_path / "agents.yaml").write_text(content)
        request = _make_request(tmp_path)
        result = await get_config(request, filename="agents.yaml")
        assert result["content"] == content
        assert result["name"] == "agents.yaml"

    async def test_disallowed_file_returns_404(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await get_config(request, filename="secret.yaml")
        assert exc_info.value.status_code == 404

    async def test_missing_file_returns_404(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await get_config(request, filename="agents.yaml")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# put_config
# ---------------------------------------------------------------------------


class TestPutConfig:
    async def test_writes_valid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "agents.yaml").write_text("old: content")
        request = _make_request(tmp_path)
        new_content = "agents:\n  pm:\n    enabled: false\n"
        result = await put_config(request, filename="agents.yaml", body={"content": new_content})
        assert result["name"] == "agents.yaml"
        assert (tmp_path / "agents.yaml").read_text() == new_content

    async def test_rejects_invalid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "agents.yaml").write_text("valid: yaml")
        request = _make_request(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await put_config(request, filename="agents.yaml", body={"content": "invalid: yaml: :"})
        assert exc_info.value.status_code == 422

    async def test_rejects_disallowed_file(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await put_config(request, filename="hack.yaml", body={"content": "ok: true"})
        assert exc_info.value.status_code == 404

    async def test_rejects_non_string_content(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await put_config(request, filename="agents.yaml", body={"content": 123})
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# list_prompts
# ---------------------------------------------------------------------------


class TestListPrompts:
    async def test_lists_md_files(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "parse_task.md").write_text("# Parse")
        (prompts_dir / "nudge.md").write_text("# Nudge")
        request = _make_request(tmp_path, project_root=tmp_path)
        result = await list_prompts(request)
        names = [f["name"] for f in result["prompts"]]
        assert "parse_task.md" in names
        assert "nudge.md" in names

    async def test_missing_prompts_dir(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path, project_root=tmp_path)
        result = await list_prompts(request)
        assert result["prompts"] == []


# ---------------------------------------------------------------------------
# get_prompt
# ---------------------------------------------------------------------------


class TestGetPrompt:
    async def test_reads_valid_prompt(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = "# Parse Task\nYou are a task parser."
        (prompts_dir / "parse_task.md").write_text(content)
        request = _make_request(tmp_path, project_root=tmp_path)
        result = await get_prompt(request, filename="parse_task.md")
        assert result["content"] == content

    async def test_rejects_non_md(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path, project_root=tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await get_prompt(request, filename="hack.yaml")
        assert exc_info.value.status_code == 400

    async def test_blocks_directory_traversal(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path, project_root=tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await get_prompt(request, filename="../etc/passwd.md")
        assert exc_info.value.status_code == 400

    async def test_missing_prompt_returns_404(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        request = _make_request(tmp_path, project_root=tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await get_prompt(request, filename="nonexistent.md")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# put_prompt
# ---------------------------------------------------------------------------


class TestPutPrompt:
    async def test_writes_prompt_file(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.md").write_text("old")
        request = _make_request(tmp_path, project_root=tmp_path)
        await put_prompt(request, filename="test.md", body={"content": "# New content"})
        assert (prompts_dir / "test.md").read_text() == "# New content"

    async def test_rejects_non_md(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path, project_root=tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await put_prompt(request, filename="hack.yaml", body={"content": "x"})
        assert exc_info.value.status_code == 400

    async def test_blocks_directory_traversal(self, tmp_path: Path) -> None:
        request = _make_request(tmp_path, project_root=tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            await put_prompt(request, filename="../evil.md", body={"content": "x"})
        assert exc_info.value.status_code == 400
