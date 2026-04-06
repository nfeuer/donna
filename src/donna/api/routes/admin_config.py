"""Config and prompt file endpoints for the Donna Management GUI.

Read-only in session 1. Write (PUT) endpoints will be added in session 2
for editing configs and prompts through the GUI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

# Allowed config files (prevent directory traversal)
_ALLOWED_CONFIGS = {
    "agents.yaml",
    "donna_models.yaml",
    "task_types.yaml",
    "task_states.yaml",
    "preferences.yaml",
    "discord.yaml",
    "calendar.yaml",
    "email.yaml",
    "sms.yaml",
}


def _get_config_dir(request: Request) -> Path:
    return Path(request.app.state.config_dir)


def _get_project_root(request: Request) -> Path:
    return Path(os.environ.get("DONNA_PROJECT_ROOT", Path(__file__).parent.parent.parent.parent.parent))


@router.get("/configs")
async def list_configs(request: Request) -> dict[str, Any]:
    """List available YAML config files with metadata."""
    config_dir = _get_config_dir(request)
    files = []
    for name in sorted(_ALLOWED_CONFIGS):
        path = config_dir / name
        if path.exists():
            stat = path.stat()
            files.append({
                "name": name,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })
    return {"configs": files, "config_dir": str(config_dir)}


@router.get("/configs/{filename}")
async def get_config(request: Request, filename: str) -> dict[str, Any]:
    """Read the content of a config file."""
    if filename not in _ALLOWED_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Config file not found: {filename}")

    config_dir = _get_config_dir(request)
    path = config_dir / filename

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {filename}")

    content = path.read_text(encoding="utf-8")
    return {
        "name": filename,
        "content": content,
        "size_bytes": path.stat().st_size,
        "modified": path.stat().st_mtime,
    }


@router.get("/prompts")
async def list_prompts(request: Request) -> dict[str, Any]:
    """List available prompt template files."""
    project_root = _get_project_root(request)
    prompts_dir = project_root / "prompts"

    if not prompts_dir.exists():
        return {"prompts": [], "prompts_dir": str(prompts_dir)}

    files = []
    for path in sorted(prompts_dir.glob("*.md")):
        stat = path.stat()
        files.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return {"prompts": files, "prompts_dir": str(prompts_dir)}


@router.get("/prompts/{filename}")
async def get_prompt(request: Request, filename: str) -> dict[str, Any]:
    """Read the content of a prompt template file."""
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Prompt files must be .md")

    # Prevent directory traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    project_root = _get_project_root(request)
    path = project_root / "prompts" / filename

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {filename}")

    content = path.read_text(encoding="utf-8")
    return {
        "name": filename,
        "content": content,
        "size_bytes": path.stat().st_size,
        "modified": path.stat().st_mtime,
    }
