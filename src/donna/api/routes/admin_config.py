"""Config and prompt file endpoints for the Donna Management GUI.

Read and write endpoints for YAML configs and prompt templates.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, HTTPException, Request

from donna.api.auth import admin_router

router = admin_router()

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
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc

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

    # Hot-reload hook for chat config
    if filename == "chat.yaml":
        from donna.chat.config import _cache, get_chat_config
        key = str(config_dir)
        _cache.pop(key, None)
        new_chat_config = get_chat_config(config_dir, cache_ttl_s=0)
        engine = getattr(request.app.state, "chat_engine", None)
        if engine is not None:
            engine._config = new_chat_config

    return {
        "name": filename,
        "size_bytes": stat.st_size,
        "modified": stat.st_mtime,
    }


@router.get("/prompts")
async def list_prompts(request: Request) -> dict[str, Any]:
    """List available prompt template files, including subdirectories."""
    project_root = _get_project_root(request)
    prompts_dir = project_root / "prompts"

    if not prompts_dir.exists():
        return {"prompts": [], "prompts_dir": str(prompts_dir)}

    files = []
    for path in sorted(prompts_dir.rglob("*.md")):
        rel = path.relative_to(prompts_dir)
        stat = path.stat()
        files.append({
            "name": str(rel),
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return {"prompts": files, "prompts_dir": str(prompts_dir)}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning empty dict on missing/invalid."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_prompt_stats(
    *,
    prompts_dir: Path,
    config_dir: Path,
    invocation_counts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build prompt stats from config files and invocation counts.

    Pure function — all I/O (DB queries) happens in the caller.

    Args:
        prompts_dir: Path to the prompts directory.
        config_dir: Path to the config directory.
        invocation_counts: Mapping of task_type to invocation/cost data.

    Returns:
        Dictionary with prompt statistics for the dashboard.
    """
    # Enumerate prompts
    all_prompts: list[dict[str, Any]] = []
    for path in sorted(prompts_dir.rglob("*.md")):
        rel = str(path.relative_to(prompts_dir))
        stat = path.stat()
        all_prompts.append({"name": rel, "modified": stat.st_mtime})

    # Folder breakdown
    by_folder: dict[str, int] = {}
    for p in all_prompts:
        folder = str(Path(p["name"]).parent)
        key = "root" if folder == "." else folder
        by_folder[key] = by_folder.get(key, 0) + 1

    # Load task_types.yaml — map prompt filename -> task_type metadata
    task_types_cfg = _load_yaml(config_dir / "task_types.yaml").get("task_types", {})
    prompt_to_task: dict[str, dict[str, str]] = {}
    for tt_name, tt_cfg in task_types_cfg.items():
        tpl = tt_cfg.get("prompt_template", "")
        rel_name = tpl.removeprefix("prompts/") if tpl.startswith("prompts/") else tpl
        if rel_name:
            prompt_to_task[rel_name] = {
                "task_type": tt_name,
                "model": tt_cfg.get("model", ""),
                "output_schema": tt_cfg.get("output_schema", ""),
            }

    # Model routing
    model_counts: dict[str, int] = {}
    for meta in prompt_to_task.values():
        model_alias = meta["model"]
        model_counts[model_alias] = model_counts.get(model_alias, 0) + 1

    # Agent coverage
    agents_cfg = _load_yaml(config_dir / "agents.yaml").get("agents", {})
    known_map: dict[str, list[str]] = {
        "pm": [
            "parse_task", "parse_task_local", "classify_priority",
            "dedup_check", "task_decompose",
        ],
        "scheduler": ["generate_reminder"],
        "research": ["prep_research"],
        "coding": [],
        "challenger": ["challenge_task"],
        "communication": [
            "generate_nudge", "generate_digest", "generate_weekly_digest",
        ],
    }
    agent_task_map: dict[str, list[str]] = {}
    for agent_name, agent_cfg in agents_cfg.items():
        agent_tools = set(agent_cfg.get("allowed_tools", []))
        mapped = set(known_map.get(agent_name, []))
        for tt_name, tt_cfg in task_types_cfg.items():
            tt_tools = set(tt_cfg.get("tools", []))
            if tt_tools and tt_tools & agent_tools:
                mapped.add(tt_name)
        agent_task_map[agent_name] = sorted(mapped)

    prompt_agents: dict[str, list[str]] = {}
    for prompt_name, meta in prompt_to_task.items():
        tt = meta["task_type"]
        agents = [a for a, tts in agent_task_map.items() if tt in tts]
        if agents:
            prompt_agents[prompt_name] = sorted(agents)

    agent_coverage = sorted(
        [{"prompt": k, "agents": v} for k, v in prompt_agents.items()],
        key=lambda x: len(x["agents"]),
        reverse=True,
    )

    # Most invoked
    most_invoked = []
    for prompt_name, meta in prompt_to_task.items():
        tt = meta["task_type"]
        counts = invocation_counts.get(tt, {})
        if counts.get("invocations", 0) > 0:
            most_invoked.append({
                "prompt": prompt_name,
                "task_type": tt,
                "invocations": counts["invocations"],
                "cost_usd": round(counts.get("cost_usd", 0), 4),
            })
    most_invoked.sort(key=lambda x: x["invocations"], reverse=True)

    # Recently modified (top 3)
    recently_modified = sorted(
        all_prompts, key=lambda x: x["modified"], reverse=True,
    )[:3]

    # Unused
    mapped_prompts = set(prompt_to_task.keys())
    unused = []
    for p in all_prompts:
        name = p["name"]
        if name not in mapped_prompts:
            unused.append(name)
        elif prompt_to_task[name]["task_type"] not in invocation_counts:
            unused.append(name)

    return {
        "total": len(all_prompts),
        "by_folder": by_folder,
        "most_invoked": most_invoked[:10],
        "agent_coverage": agent_coverage,
        "model_routing": model_counts,
        "recently_modified": recently_modified,
        "unused": unused,
    }


@router.get("/prompts/stats")
async def get_prompt_stats(request: Request) -> dict[str, Any]:
    """Prompt usage stats for the welcome dashboard."""
    project_root = _get_project_root(request)
    prompts_dir = project_root / "prompts"
    config_dir = _get_config_dir(request)

    invocation_counts: dict[str, dict[str, Any]] = {}
    try:
        conn = request.app.state.db.connection
        cursor = await conn.execute(
            """SELECT task_type, COUNT(*), COALESCE(SUM(cost_usd), 0)
               FROM invocation_log
               GROUP BY task_type"""
        )
        for row in await cursor.fetchall():
            invocation_counts[row[0]] = {
                "invocations": row[1],
                "cost_usd": float(row[2]),
            }
    except Exception:
        pass

    return _build_prompt_stats(
        prompts_dir=prompts_dir,
        config_dir=config_dir,
        invocation_counts=invocation_counts,
    )


@router.get("/prompts/{filename:path}")
async def get_prompt(request: Request, filename: str) -> dict[str, Any]:
    """Read the content of a prompt template file."""
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Prompt files must be .md")

    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    project_root = _get_project_root(request)
    path = (project_root / "prompts" / filename).resolve()

    # Ensure resolved path stays within prompts directory
    prompts_dir = (project_root / "prompts").resolve()
    if not path.is_relative_to(prompts_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {filename}")

    content = path.read_text(encoding="utf-8")

    # Reverse-lookup: which task_type uses this prompt?
    config_dir = _get_config_dir(request)
    task_types_cfg = _load_yaml(config_dir / "task_types.yaml").get("task_types", {})
    task_type = None
    model_alias = None
    output_schema = None
    for tt_name, tt_cfg in task_types_cfg.items():
        tpl = tt_cfg.get("prompt_template", "")
        rel_name = tpl.removeprefix("prompts/") if tpl.startswith("prompts/") else tpl
        if rel_name == filename:
            task_type = tt_name
            model_alias = tt_cfg.get("model")
            output_schema = tt_cfg.get("output_schema")
            break

    return {
        "name": filename,
        "content": content,
        "size_bytes": path.stat().st_size,
        "modified": path.stat().st_mtime,
        "task_type": task_type,
        "model_alias": model_alias,
        "output_schema": output_schema,
    }


@router.put("/prompts/{filename:path}")
async def put_prompt(
    request: Request,
    filename: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Write a prompt template file."""
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Prompt files must be .md")
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    project_root = _get_project_root(request)
    prompts_dir = (project_root / "prompts").resolve()
    path = (prompts_dir / filename).resolve()

    if not path.is_relative_to(prompts_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Atomic write — use the file's parent dir for the temp file
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
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
