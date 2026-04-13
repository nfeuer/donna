"""Config and prompt file endpoints for the Donna Management GUI.

Read and write endpoints for YAML configs and prompt templates.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException, Request

router = APIRouter()

# Allowed config files (prevent directory traversal)
_ALLOWED_CONFIGS = {
    "agents.yaml",
    "calendar.yaml",
    "chat.yaml",
    "dashboard.yaml",
    "discord.yaml",
    "donna_models.yaml",
    "email.yaml",
    "llm_gateway.yaml",
    "preferences.yaml",
    "sms.yaml",
    "task_states.yaml",
    "task_types.yaml",
}


def _get_config_dir(request: Request) -> Path:
    return Path(request.app.state.config_dir)


def _find_project_root() -> Path:
    """Walk up from this file to find the project root (contains pyproject.toml)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "pyproject.toml").exists():
            return current
        if current == current.parent:
            break
        current = current.parent
    raise RuntimeError("Could not locate project root (pyproject.toml not found)")


def _get_project_root(request: Request) -> Path:
    env = os.environ.get("DONNA_PROJECT_ROOT")
    if env:
        return Path(env)
    return _find_project_root()


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


@router.put("/configs/{filename}")
async def put_config(
    request: Request,
    filename: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Write a config file after validating YAML syntax."""
    if filename not in _ALLOWED_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Config file not allowed: {filename}")

    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    # Validate YAML syntax
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}")

    config_dir = _get_config_dir(request)
    path = config_dir / filename

    # Atomic write: write to temp file then rename
    fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    stat = path.stat()

    # Hot-reload hook for gateway config
    if filename == "llm_gateway.yaml":
        from donna.llm.types import load_gateway_config
        new_config = load_gateway_config(config_dir)
        queue = getattr(request.app.state, "llm_queue", None)
        if queue:
            queue.reload_config(new_config)
        request.app.state.llm_gateway_config = new_config

    return {
        "name": filename,
        "size_bytes": stat.st_size,
        "modified": stat.st_mtime,
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


@router.put("/prompts/{filename}")
async def put_prompt(
    request: Request,
    filename: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Write a prompt template file."""
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Prompt files must be .md")
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    project_root = _get_project_root(request)
    prompts_dir = project_root / "prompts"
    path = prompts_dir / filename

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(dir=str(prompts_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    stat = path.stat()
    return {
        "name": filename,
        "size_bytes": stat.st_size,
        "modified": stat.st_mtime,
    }
