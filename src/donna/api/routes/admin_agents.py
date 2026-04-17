"""Agent detail endpoints for the Donna Management GUI.

Merges static config from agents.yaml with live metrics from invocation_log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException, Request

from donna.api.auth import admin_router

router = admin_router()


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_agent_task_map(config_dir: Path) -> dict[str, list[str]]:
    """Map agent names to task_types they handle.

    Heuristic: an agent handles task_types whose tools overlap with
    the agent's allowed_tools, plus well-known mappings.
    """
    agents_cfg = _load_yaml(config_dir / "agents.yaml").get("agents", {})
    task_types_cfg = _load_yaml(config_dir / "task_types.yaml").get("task_types", {})

    # Well-known agent → task_type mappings
    known_map: dict[str, list[str]] = {
        "pm": ["parse_task", "parse_task_local", "classify_priority", "dedup_check", "task_decompose"],
        "scheduler": ["generate_reminder"],
        "research": ["prep_research"],
        "coding": [],
        "challenger": ["challenge_task"],
        "communication": ["generate_nudge", "generate_digest", "generate_weekly_digest"],
    }

    # Merge: start with known, then add any task_types with matching tools
    result: dict[str, list[str]] = {}
    for agent_name, agent_cfg in agents_cfg.items():
        agent_tools = set(agent_cfg.get("allowed_tools", []))
        mapped = set(known_map.get(agent_name, []))

        for tt_name, tt_cfg in task_types_cfg.items():
            tt_tools = set(tt_cfg.get("tools", []))
            if tt_tools and tt_tools & agent_tools:
                mapped.add(tt_name)

        result[agent_name] = sorted(mapped)

    return result


@router.get("/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    """List all agents with config and summary metrics."""
    config_dir = Path(request.app.state.config_dir)
    agents_cfg = _load_yaml(config_dir / "agents.yaml").get("agents", {})
    agent_task_map = _build_agent_task_map(config_dir)

    conn = request.app.state.db.connection

    agents = []
    for name, cfg in agents_cfg.items():
        task_types = agent_task_map.get(name, [])

        # Query summary metrics for this agent's task_types
        metrics = {"total_calls": 0, "avg_latency_ms": 0, "total_cost_usd": 0.0, "last_invocation": None}
        if task_types:
            placeholders = ",".join("?" for _ in task_types)
            # Safe: {placeholders} is built from static "?" chars; values go through params
            cursor = await conn.execute(
                f"""SELECT COUNT(*), COALESCE(AVG(latency_ms), 0),
                           COALESCE(SUM(cost_usd), 0), MAX(timestamp)
                    FROM invocation_log
                    WHERE task_type IN ({placeholders})""",
                task_types,
            )
            row = await cursor.fetchone()
            if row:
                metrics = {
                    "total_calls": row[0],
                    "avg_latency_ms": round(row[1], 1),
                    "total_cost_usd": round(row[2], 4),
                    "last_invocation": row[3],
                }

        agents.append({
            "name": name,
            "enabled": cfg.get("enabled", False),
            "timeout_seconds": cfg.get("timeout_seconds", 0),
            "autonomy": cfg.get("autonomy", "low"),
            "allowed_tools": cfg.get("allowed_tools", []),
            "task_types": task_types,
            **metrics,
        })

    return {"agents": agents}


@router.get("/agents/{name}")
async def get_agent_detail(request: Request, name: str) -> dict[str, Any]:
    """Detailed agent view with activity feed, performance, and cost."""
    config_dir = Path(request.app.state.config_dir)
    agents_cfg = _load_yaml(config_dir / "agents.yaml").get("agents", {})

    if name not in agents_cfg:
        raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

    cfg = agents_cfg[name]
    agent_task_map = _build_agent_task_map(config_dir)
    task_types = agent_task_map.get(name, [])

    conn = request.app.state.db.connection
    result: dict[str, Any] = {
        "name": name,
        "enabled": cfg.get("enabled", False),
        "timeout_seconds": cfg.get("timeout_seconds", 0),
        "autonomy": cfg.get("autonomy", "low"),
        "allowed_tools": cfg.get("allowed_tools", []),
        "task_types": task_types,
    }

    if not task_types:
        result.update({
            "recent_invocations": [],
            "daily_latency": [],
            "tool_usage": [],
            "cost_summary": {"total_cost_usd": 0, "avg_cost_per_call": 0, "total_calls": 0},
        })
        return result

    placeholders = ",".join("?" for _ in task_types)

    # Recent invocations
    cursor = await conn.execute(
        f"""SELECT id, timestamp, task_type, model_alias, latency_ms,
                   tokens_in, tokens_out, cost_usd, is_shadow, task_id
            FROM invocation_log
            WHERE task_type IN ({placeholders})
            ORDER BY timestamp DESC LIMIT 50""",
        task_types,
    )
    result["recent_invocations"] = [
        {
            "id": r[0], "timestamp": r[1], "task_type": r[2],
            "model_alias": r[3], "latency_ms": r[4],
            "tokens_in": r[5], "tokens_out": r[6],
            "cost_usd": round(float(r[7]), 4), "is_shadow": bool(r[8]),
            "task_id": r[9],
        }
        for r in await cursor.fetchall()
    ]

    # Daily latency time series (last 30 days)
    cursor = await conn.execute(
        f"""SELECT DATE(timestamp) as day,
                   AVG(latency_ms) as avg_latency,
                   COUNT(*) as calls
            FROM invocation_log
            WHERE task_type IN ({placeholders})
              AND timestamp >= DATE('now', '-30 days')
            GROUP BY DATE(timestamp)
            ORDER BY day""",
        task_types,
    )
    result["daily_latency"] = [
        {"date": r[0], "avg_latency_ms": round(r[1], 1), "calls": r[2]}
        for r in await cursor.fetchall()
    ]

    # Tool usage — extract from invocation output JSON
    cursor = await conn.execute(
        f"""SELECT output FROM invocation_log
            WHERE task_type IN ({placeholders})
              AND output IS NOT NULL
            ORDER BY timestamp DESC LIMIT 200""",
        task_types,
    )
    tool_counts: dict[str, int] = {}
    for (output_raw,) in await cursor.fetchall():
        try:
            output = json.loads(output_raw) if isinstance(output_raw, str) else output_raw
            if isinstance(output, dict):
                for tool in output.get("tools_called", []):
                    tool_name = tool if isinstance(tool, str) else tool.get("name", "unknown")
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    result["tool_usage"] = [{"tool": k, "count": v} for k, v in sorted(tool_counts.items(), key=lambda x: -x[1])]

    # Cost summary
    cursor = await conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(AVG(cost_usd), 0)
            FROM invocation_log
            WHERE task_type IN ({placeholders})""",
        task_types,
    )
    row = await cursor.fetchone()
    result["cost_summary"] = {
        "total_calls": row[0],
        "total_cost_usd": round(row[1], 4),
        "avg_cost_per_call": round(row[2], 4),
    }

    return result
