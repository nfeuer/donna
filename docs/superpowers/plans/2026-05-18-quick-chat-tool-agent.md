# Quick Chat Tool Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rigid classify-dispatch-respond chat pipeline with a tool-use agent loop so Quick Chat can query any system data, answer grounded questions, and request user confirmation for writes.

**Architecture:** The engine gets a new `_run_tool_loop` method that iterates: build prompt with tool schemas, call LLM, parse JSON response, validate and execute tool calls, append results, repeat until text response or limits. Read tools auto-execute; write tools pause for confirmation. A ToolRegistry loads tool schemas from `config/chat_tools.yaml` and discovers handler functions. Per-turn structured logs carry a trace_id for Inspector integration.

**Tech Stack:** Python 3.12 + asyncio, aiosqlite, structlog, Pydantic, YAML config, Jinja2 templates, React 18 + TypeScript + CSS Modules.

**Spec:** `docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md`

---

## File Structure

```
src/donna/chat/
  engine.py                    # Modified: add _run_tool_loop, force_new param, trace_id logging
  tools/
    __init__.py                # ToolRegistry + ToolResult + ToolContext types
    invocations.py             # query_invocations, get_invocation_detail, query_invocation_stats
    tasks.py                   # query_tasks, get_task_detail
    automations.py             # query_automations, get_automation_detail
    skills.py                  # query_skills, get_skill_detail, query_skill_candidates
    vault.py                   # list_vault_files, read_vault_file (wraps existing)
    system.py                  # get_system_health, query_preferences
  types.py                     # Modified: add trace_id, invocation_ids to ChatMessage + ChatResponse

config/
  chat_tools.yaml              # Tool schemas (read + write), loaded by ToolRegistry

prompts/chat/
  tool_agent_system.md         # New system prompt template with tool-use instructions

alembic/versions/
  add_trace_id_columns.py      # Migration: trace_id on conversation_messages + invocation_log

donna-ui/src/
  api/chat.ts                  # Modified: add trace_id to ChatMessage type
  pages/Chat/MessageThread.tsx # Modified: debug icon on assistant messages
  pages/Chat/Chat.module.css   # Modified: add debugLink styles

tests/
  unit/test_tool_registry.py   # ToolRegistry load, validate, execute
  unit/test_tool_loop.py       # Tool loop parse, limits, error handling
  unit/test_tool_invocations.py # Invocation tool handlers
  unit/test_tool_tasks.py      # Task tool handlers
  unit/test_tool_system.py     # System + preferences tool handlers
```

---

### Task 1: ToolRegistry + ToolResult types

**Files:**
- Create: `src/donna/chat/tools/__init__.py`
- Create: `config/chat_tools.yaml`
- Test: `tests/unit/test_tool_registry.py`

- [ ] **Step 1: Write the failing test for ToolRegistry.from_yaml**

```python
# tests/unit/test_tool_registry.py
"""Tests for the chat tool registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from donna.chat.tools import ToolContext, ToolRegistry, ToolResult


@pytest.fixture
def tools_yaml(tmp_path: Path) -> Path:
    config = {
        "tools": {
            "query_tasks": {
                "description": "Search tasks by status",
                "domain": "tasks",
                "type": "read",
                "handler": "donna.chat.tools.tasks.query_tasks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "default": 25, "maximum": 100},
                    },
                    "required": [],
                },
            },
            "create_task": {
                "description": "Create a new task",
                "domain": "tasks",
                "type": "write",
                "handler": "donna.chat.actions.tasks.create_task",
                "parameters": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
        }
    }
    path = tmp_path / "chat_tools.yaml"
    path.write_text(yaml.dump(config))
    return path


class TestToolRegistry:
    def test_loads_tools_from_yaml(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert len(registry.list_tools()) == 2

    def test_get_tool_by_name(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        tool = registry.get("query_tasks")
        assert tool is not None
        assert tool.domain == "tasks"
        assert tool.tool_type == "read"

    def test_get_unknown_tool_returns_none(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert registry.get("nonexistent") is None

    def test_is_read_tool(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert registry.is_read_tool("query_tasks") is True
        assert registry.is_read_tool("create_task") is False

    def test_schemas_for_prompt(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        schemas = registry.schemas_for_prompt()
        assert "query_tasks" in schemas
        assert "create_task" in schemas
        assert "Search tasks by status" in schemas

    def test_validate_params_valid(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("query_tasks", {"status": "active"})
        assert errors is None

    def test_validate_params_missing_required(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("create_task", {})
        assert errors is not None
        assert "title" in errors

    def test_validate_params_unknown_tool(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("nonexistent", {})
        assert errors is not None
        assert "Unknown tool" in errors

    def test_missing_yaml_returns_empty_registry(self, tmp_path: Path) -> None:
        registry = ToolRegistry.from_yaml(tmp_path / "missing.yaml")
        assert len(registry.list_tools()) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/donna/donna && python3 -m pytest tests/unit/test_tool_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat.tools'`

- [ ] **Step 3: Write ToolRegistry, ToolResult, ToolContext, and ToolDefinition**

```python
# src/donna/chat/tools/__init__.py
"""Tool registry for the chat tool-use agent loop.

Loads tool schemas from config/chat_tools.yaml, validates parameters,
resolves handlers, and executes tool calls.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class ToolResult:
    """Standardized result from a read tool execution."""

    results: list[dict[str, Any]]
    total_count: int
    truncated: bool = False


@dataclasses.dataclass(frozen=True)
class ToolContext:
    """Context passed to every tool handler."""

    db: Any
    user_id: str
    session_id: str


@dataclasses.dataclass(frozen=True)
class ToolDefinition:
    """Single tool from chat_tools.yaml."""

    name: str
    description: str
    domain: str
    tool_type: str  # "read" | "write"
    handler: str  # dotted path
    parameters: dict[str, Any] = dataclasses.field(default_factory=dict)


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]

MAX_RESULT_TOKENS = 4000
TRUNCATION_TARGET_TOKENS = 3500


class ToolRegistry:
    """Loads and manages chat tool definitions from YAML config."""

    def __init__(self, tools: dict[str, ToolDefinition]) -> None:
        self._tools = tools
        self._handlers: dict[str, ToolHandler] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> ToolRegistry:
        if not path.exists():
            logger.warning("chat_tools_config_not_found", path=str(path))
            return cls({})
        raw = yaml.safe_load(path.read_text()) or {}
        tools_raw = raw.get("tools", {})
        tools: dict[str, ToolDefinition] = {}
        for name, defn in tools_raw.items():
            tools[name] = ToolDefinition(
                name=name,
                description=defn.get("description", ""),
                domain=defn.get("domain", ""),
                tool_type=defn.get("type", "read"),
                handler=defn.get("handler", ""),
                parameters=defn.get("parameters", {}),
            )
        logger.info("tool_registry_loaded", count=len(tools))
        return cls(tools)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def is_read_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.tool_type == "read"

    def schemas_for_prompt(self) -> str:
        """Format all tool schemas for inclusion in the system prompt."""
        lines: list[str] = []
        for tool in self._tools.values():
            lines.append(f"### {tool.name}")
            lines.append(f"Description: {tool.description}")
            lines.append(f"Type: {tool.tool_type}")
            params = tool.parameters.get("properties", {})
            required = tool.parameters.get("required", [])
            if params:
                lines.append("Parameters:")
                for pname, pschema in params.items():
                    req = " (required)" if pname in required else ""
                    ptype = pschema.get("type", "any")
                    desc = pschema.get("description", "")
                    enum_vals = pschema.get("enum")
                    default = pschema.get("default")
                    detail = f"  - {pname}: {ptype}{req}"
                    if enum_vals:
                        detail += f" — one of: {', '.join(str(v) for v in enum_vals)}"
                    if desc:
                        detail += f" — {desc}"
                    if default is not None:
                        detail += f" (default: {default})"
                    lines.append(detail)
            else:
                lines.append("Parameters: none")
            lines.append("")
        return "\n".join(lines)

    def validate_params(self, tool_name: str, params: dict[str, Any]) -> str | None:
        """Validate params against the tool schema. Returns error string or None."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return f"Unknown tool: {tool_name}"
        required = tool.parameters.get("required", [])
        missing = [r for r in required if r not in params or params[r] is None]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None

    def _resolve_handler(self, tool: ToolDefinition) -> ToolHandler:
        if tool.name in self._handlers:
            return self._handlers[tool.name]
        module_path, func_name = tool.handler.rsplit(".", 1)
        module = importlib.import_module(module_path)
        handler: ToolHandler = getattr(module, func_name)
        self._handlers[tool.name] = handler
        return handler

    async def execute(
        self, tool_name: str, params: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        """Execute a tool and return its result."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(results=[], total_count=0)
        handler = self._resolve_handler(tool)
        return await handler(params, ctx)


def truncate_result(result: ToolResult) -> tuple[str, bool]:
    """Serialize a ToolResult to JSON, truncating if over token budget.

    Returns (json_string, was_truncated).
    """
    data = {
        "results": result.results,
        "total_count": result.total_count,
        "truncated": result.truncated,
    }
    serialized = json.dumps(data, default=str)
    estimated_tokens = len(serialized) // 4

    if estimated_tokens <= MAX_RESULT_TOKENS:
        return serialized, False

    rows = list(result.results)
    while rows and len(json.dumps({"results": rows, "total_count": result.total_count, "truncated": True}, default=str)) // 4 > TRUNCATION_TARGET_TOKENS:
        rows.pop()

    truncated_data = {
        "results": rows,
        "total_count": result.total_count,
        "truncated": True,
    }
    serialized = json.dumps(truncated_data, default=str)
    notice = f"\n[Truncated: showing first {len(rows)} of {len(result.results)} rows. {result.total_count} total matching records. Refine your query or request specific IDs.]"
    return serialized + notice, True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_registry.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Write the chat_tools.yaml config**

```yaml
# config/chat_tools.yaml
# Tool schemas for the chat tool-use agent loop.
# Each tool is auto-discovered by ToolRegistry at startup.
# See docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md §3 and §14.

tools:
  # ── Invocations & Logs ────────────────────────────────
  query_invocations:
    description: "Search the invocation log for LLM calls by date, model, cost, or error status"
    domain: invocations
    type: read
    handler: donna.chat.tools.invocations.query_invocations
    parameters:
      type: object
      properties:
        date_from:
          type: string
          description: "ISO date (YYYY-MM-DD) start filter"
        date_to:
          type: string
          description: "ISO date end filter"
        task_type:
          type: string
          description: "Exact match on task_type"
        model:
          type: string
          description: "Model alias or actual model ID"
        min_cost:
          type: number
          description: "Minimum cost_usd"
        min_latency:
          type: integer
          description: "Minimum latency_ms"
        has_error:
          type: boolean
          description: "Filter to errored invocations"
        sort:
          type: string
          enum: [cost, latency, timestamp, tokens_in]
          default: timestamp
        sort_dir:
          type: string
          enum: [asc, desc]
          default: desc
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  get_invocation_detail:
    description: "Get full details for a single LLM invocation by ID"
    domain: invocations
    type: read
    handler: donna.chat.tools.invocations.get_invocation_detail
    parameters:
      type: object
      properties:
        invocation_id:
          type: string
      required: [invocation_id]

  query_invocation_stats:
    description: "Get aggregated invocation statistics grouped by task_type, model, or date"
    domain: invocations
    type: read
    handler: donna.chat.tools.invocations.query_invocation_stats
    parameters:
      type: object
      properties:
        group_by:
          type: string
          enum: [task_type, model, date]
        date_from:
          type: string
          description: "ISO date start"
        date_to:
          type: string
          description: "ISO date end"
      required: [group_by]

  # ── Tasks ─────────────────────────────────────────────
  query_tasks:
    description: "Search tasks by status, priority, domain, or title keyword"
    domain: tasks
    type: read
    handler: donna.chat.tools.tasks.query_tasks
    parameters:
      type: object
      properties:
        status:
          type: string
          description: "Filter by task status"
        priority:
          type: integer
          description: "Filter by priority (0-3)"
        domain:
          type: string
          description: "Filter by domain"
        title_search:
          type: string
          description: "Substring match on title"
        created_after:
          type: string
          description: "ISO date"
        updated_after:
          type: string
          description: "ISO date"
        sort:
          type: string
          enum: [priority, created_at, updated_at, deadline]
          default: priority
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  get_task_detail:
    description: "Get full details for a single task by ID"
    domain: tasks
    type: read
    handler: donna.chat.tools.tasks.get_task_detail
    parameters:
      type: object
      properties:
        task_id:
          type: string
      required: [task_id]

  # ── Automations ───────────────────────────────────────
  query_automations:
    description: "List automations, optionally filtered to active only or by skill"
    domain: automations
    type: read
    handler: donna.chat.tools.automations.query_automations
    parameters:
      type: object
      properties:
        active_only:
          type: boolean
          default: true
        skill_name:
          type: string
          description: "Filter by associated skill"
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  get_automation_detail:
    description: "Get full config and recent run history for an automation"
    domain: automations
    type: read
    handler: donna.chat.tools.automations.get_automation_detail
    parameters:
      type: object
      properties:
        automation_id:
          type: string
      required: [automation_id]

  # ── Skills ────────────────────────────────────────────
  query_skills:
    description: "List skills by status with run counts and quality scores"
    domain: skills
    type: read
    handler: donna.chat.tools.skills.query_skills
    parameters:
      type: object
      properties:
        status:
          type: string
          enum: [active, candidate, shadow, draft]
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  get_skill_detail:
    description: "Get full config and recent runs for a skill"
    domain: skills
    type: read
    handler: donna.chat.tools.skills.get_skill_detail
    parameters:
      type: object
      properties:
        skill_name:
          type: string
      required: [skill_name]

  query_skill_candidates:
    description: "List candidate skills with confidence scores and recommendations"
    domain: skills
    type: read
    handler: donna.chat.tools.skills.query_skill_candidates
    parameters:
      type: object
      properties:
        status:
          type: string
          enum: [pending, approved, rejected]
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  # ── Vault ─────────────────────────────────────────────
  list_vault_files:
    description: "List files in the vault, optionally within a folder"
    domain: vault
    type: read
    handler: donna.chat.tools.vault.list_vault_files
    parameters:
      type: object
      properties:
        folder:
          type: string
      required: []

  read_vault_file:
    description: "Read a file from the vault by path"
    domain: vault
    type: read
    handler: donna.chat.tools.vault.read_vault_file
    parameters:
      type: object
      properties:
        path:
          type: string
          description: "Relative path within the vault"
      required: [path]

  # ── System ────────────────────────────────────────────
  get_system_health:
    description: "Get system status overview including queue depth, errors, and uptime"
    domain: system
    type: read
    handler: donna.chat.tools.system.get_system_health
    parameters:
      type: object
      properties: {}
      required: []

  query_preferences:
    description: "List learned preference rules by type"
    domain: system
    type: read
    handler: donna.chat.tools.system.query_preferences
    parameters:
      type: object
      properties:
        rule_type:
          type: string
          description: "Filter by type (scheduling, priority, etc.)"
        enabled_only:
          type: boolean
          default: true
        limit:
          type: integer
          default: 25
          maximum: 100
      required: []

  # ── Write tools (existing action handlers) ────────────
  create_task:
    description: "Create a new task"
    domain: tasks
    type: write
    handler: donna.chat.actions.tasks.create_task
    parameters:
      type: object
      properties:
        title:
          type: string
        description:
          type: string
        priority:
          type: string
          enum: [P0, P1, P2, P3]
        domain:
          type: string
          enum: [personal, work, family]
      required: [title]

  update_task:
    description: "Update a task's status, priority, or notes"
    domain: tasks
    type: write
    handler: donna.chat.actions.tasks.update_task
    parameters:
      type: object
      properties:
        task_id:
          type: string
        status:
          type: string
        priority:
          type: string
        notes:
          type: string
      required: [task_id]

  reschedule_task:
    description: "Reschedule a task to a new date"
    domain: tasks
    type: write
    handler: donna.chat.actions.tasks.reschedule_task
    parameters:
      type: object
      properties:
        task_id:
          type: string
        scheduled_start:
          type: string
          description: "ISO 8601 date or datetime"
      required: [task_id, scheduled_start]

  create_vault_note:
    description: "Create a new note file in the vault"
    domain: vault
    type: write
    handler: donna.chat.actions.vault.create_vault_note
    parameters:
      type: object
      properties:
        title:
          type: string
        content:
          type: string
        folder:
          type: string
      required: [title, content]

  create_automation:
    description: "Create a new automation rule"
    domain: automations
    type: write
    handler: donna.chat.actions.automations.create_automation
    parameters:
      type: object
      properties:
        name:
          type: string
        trigger:
          type: string
        skill_name:
          type: string
      required: [name, trigger, skill_name]

  execute_skill:
    description: "Run a skill and report results"
    domain: skills
    type: write
    handler: donna.chat.actions.skills.execute_skill
    parameters:
      type: object
      properties:
        skill_name:
          type: string
        input_data:
          type: object
      required: [skill_name]

  create_skill_draft:
    description: "Draft a new skill definition"
    domain: skills
    type: write
    handler: donna.chat.actions.skills.create_skill_draft
    parameters:
      type: object
      properties:
        name:
          type: string
        description:
          type: string
        steps:
          type: array
          items:
            type: string
      required: [name, description]
```

- [ ] **Step 6: Commit**

```bash
git add src/donna/chat/tools/__init__.py config/chat_tools.yaml tests/unit/test_tool_registry.py
git commit -m "feat(chat): add ToolRegistry with YAML-driven schema loading and validation"
```

---

### Task 2: Read tool handlers — invocations

**Files:**
- Create: `src/donna/chat/tools/invocations.py`
- Test: `tests/unit/test_tool_invocations.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tool_invocations.py
"""Tests for invocation read tool handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.invocations import (
    get_invocation_detail,
    query_invocation_stats,
    query_invocations,
)


@pytest.fixture
def ctx() -> ToolContext:
    db = AsyncMock()
    db.execute_sql = AsyncMock(return_value=[])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


class TestQueryInvocations:
    async def test_returns_tool_result(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {
                "id": "inv-1",
                "task_type": "chat_respond",
                "model_alias": "local",
                "model_actual": "qwen2.5:32b",
                "tokens_in": 500,
                "tokens_out": 100,
                "cost_usd": 0.0001,
                "latency_ms": 2500,
                "quality_score": None,
                "timestamp": "2026-05-17T10:00:00",
                "has_error": False,
            },
        ]
        result = await query_invocations({"limit": 10}, ctx)
        assert isinstance(result, ToolResult)
        assert len(result.results) == 1
        assert result.results[0]["id"] == "inv-1"
        assert result.total_count >= 1

    async def test_applies_date_filters(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        await query_invocations(
            {"date_from": "2026-05-17", "date_to": "2026-05-18"}, ctx,
        )
        call_args = ctx.db.execute_sql.call_args
        sql = call_args[0][0] if call_args[0] else call_args[1].get("sql", "")
        assert "timestamp" in sql.lower()

    async def test_default_limit_25(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        await query_invocations({}, ctx)
        call_args = ctx.db.execute_sql.call_args
        sql = call_args[0][0]
        assert "25" in sql or "LIMIT" in sql.upper()


class TestGetInvocationDetail:
    async def test_returns_single_invocation(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {
                "id": "inv-1",
                "task_type": "chat_respond",
                "model_alias": "local",
                "model_actual": "qwen2.5:32b",
                "tokens_in": 500,
                "tokens_out": 100,
                "cost_usd": 0.0001,
                "latency_ms": 2500,
                "timestamp": "2026-05-17T10:00:00",
                "payload_path": "data/payloads/2026-05-17/inv-1.json",
            },
        ]
        result = await get_invocation_detail({"invocation_id": "inv-1"}, ctx)
        assert result.total_count == 1
        assert result.results[0]["payload_path"] is not None

    async def test_not_found(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        result = await get_invocation_detail({"invocation_id": "missing"}, ctx)
        assert result.total_count == 0


class TestQueryInvocationStats:
    async def test_grouped_by_task_type(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {
                "group_key": "chat_respond",
                "count": 42,
                "total_cost": 0.005,
                "avg_cost": 0.00012,
                "avg_latency": 2500,
                "total_tokens_in": 21000,
                "total_tokens_out": 4200,
            },
        ]
        result = await query_invocation_stats({"group_by": "task_type"}, ctx)
        assert result.total_count == 1
        assert result.results[0]["group_key"] == "chat_respond"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_invocations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat.tools.invocations'`

- [ ] **Step 3: Write the invocation tool handlers**

```python
# src/donna/chat/tools/invocations.py
"""Read tool handlers for invocation log queries."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def query_invocations(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    sort = params.get("sort", "timestamp")
    sort_dir = params.get("sort_dir", "desc")

    sort_map = {
        "cost": "cost_usd",
        "latency": "latency_ms",
        "timestamp": "timestamp",
        "tokens_in": "tokens_in",
    }
    order_col = sort_map.get(sort, "timestamp")

    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("date_from"):
        conditions.append("timestamp >= ?")
        bind_params.append(params["date_from"])
    if params.get("date_to"):
        conditions.append("timestamp < ?")
        bind_params.append(params["date_to"])
    if params.get("task_type"):
        conditions.append("task_type = ?")
        bind_params.append(params["task_type"])
    if params.get("model"):
        conditions.append("(model_alias = ? OR model_actual = ?)")
        bind_params.extend([params["model"], params["model"]])
    if params.get("min_cost") is not None:
        conditions.append("cost_usd >= ?")
        bind_params.append(params["min_cost"])
    if params.get("min_latency") is not None:
        conditions.append("latency_ms >= ?")
        bind_params.append(params["min_latency"])
    if params.get("has_error") is not None:
        if params["has_error"]:
            conditions.append("output LIKE '%error%'")
        else:
            conditions.append("(output IS NULL OR output NOT LIKE '%error%')")

    where = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as cnt FROM invocation_log WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT id, task_type, model_alias, model_actual, tokens_in, tokens_out,
              cost_usd, latency_ms, quality_score, timestamp,
              CASE WHEN output LIKE '%error%' THEN 1 ELSE 0 END as has_error
              FROM invocation_log WHERE {where}
              ORDER BY {order_col} {sort_dir.upper()}
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)


async def get_invocation_detail(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    invocation_id = params["invocation_id"]
    sql = """SELECT id, task_type, task_id, model_alias, model_actual,
             tokens_in, tokens_out, cost_usd, latency_ms, quality_score,
             timestamp, user_id, payload_path, input_hash,
             estimated_tokens_in, overflow_escalated, trace_id
             FROM invocation_log WHERE id = ?"""
    rows = await ctx.db.execute_sql(sql, [invocation_id])
    return ToolResult(results=rows, total_count=len(rows))


async def query_invocation_stats(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    group_by = params["group_by"]
    group_col_map = {
        "task_type": "task_type",
        "model": "model_alias",
        "date": "DATE(timestamp)",
    }
    group_col = group_col_map.get(group_by, "task_type")

    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("date_from"):
        conditions.append("timestamp >= ?")
        bind_params.append(params["date_from"])
    if params.get("date_to"):
        conditions.append("timestamp < ?")
        bind_params.append(params["date_to"])

    where = " AND ".join(conditions) if conditions else "1=1"

    sql = f"""SELECT {group_col} as group_key,
              COUNT(*) as count,
              SUM(cost_usd) as total_cost,
              AVG(cost_usd) as avg_cost,
              AVG(latency_ms) as avg_latency,
              AVG(quality_score) as avg_quality,
              SUM(tokens_in) as total_tokens_in,
              SUM(tokens_out) as total_tokens_out
              FROM invocation_log WHERE {where}
              GROUP BY {group_col}
              ORDER BY count DESC"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=len(rows))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_invocations.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/chat/tools/invocations.py tests/unit/test_tool_invocations.py
git commit -m "feat(chat): add invocation read tools — query, detail, and stats"
```

---

### Task 3: Read tool handlers — tasks, automations, skills, vault, system

**Files:**
- Create: `src/donna/chat/tools/tasks.py`
- Create: `src/donna/chat/tools/automations.py`
- Create: `src/donna/chat/tools/skills.py`
- Create: `src/donna/chat/tools/vault.py`
- Create: `src/donna/chat/tools/system.py`
- Test: `tests/unit/test_tool_tasks.py`
- Test: `tests/unit/test_tool_system.py`

- [ ] **Step 1: Write the failing tests for tasks tools**

```python
# tests/unit/test_tool_tasks.py
"""Tests for task read tool handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.tasks import get_task_detail, query_tasks


@pytest.fixture
def ctx() -> ToolContext:
    db = AsyncMock()
    db.execute_sql = AsyncMock(return_value=[])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


class TestQueryTasks:
    async def test_returns_tool_result(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {"id": "t-1", "title": "Fix bug", "status": "in_progress",
             "priority": 1, "domain": "work", "created_at": "2026-05-17",
             "updated_at": "2026-05-17", "deadline": None},
        ]
        result = await query_tasks({"status": "in_progress"}, ctx)
        assert isinstance(result, ToolResult)
        assert result.results[0]["title"] == "Fix bug"

    async def test_title_search(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        await query_tasks({"title_search": "bug"}, ctx)
        sql = ctx.db.execute_sql.call_args[0][0]
        assert "LIKE" in sql.upper()

    async def test_default_sort_by_priority(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        await query_tasks({}, ctx)
        sql = ctx.db.execute_sql.call_args[0][0]
        assert "priority" in sql.lower()


class TestGetTaskDetail:
    async def test_returns_single_task(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {"id": "t-1", "title": "Fix bug", "description": "Login page broken",
             "status": "in_progress", "priority": 1, "domain": "work",
             "notes": "Check auth middleware", "created_at": "2026-05-17",
             "updated_at": "2026-05-17", "scheduled_start": None,
             "deadline": None},
        ]
        result = await get_task_detail({"task_id": "t-1"}, ctx)
        assert result.total_count == 1
        assert result.results[0]["description"] == "Login page broken"

    async def test_not_found(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        result = await get_task_detail({"task_id": "missing"}, ctx)
        assert result.total_count == 0
```

- [ ] **Step 2: Write the failing tests for system tools**

```python
# tests/unit/test_tool_system.py
"""Tests for system and preferences read tool handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.system import get_system_health, query_preferences


@pytest.fixture
def ctx() -> ToolContext:
    db = AsyncMock()
    db.execute_sql = AsyncMock(return_value=[])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


class TestGetSystemHealth:
    async def test_returns_system_metrics(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.side_effect = [
            [{"cnt": 5}],   # error_count_1h
            [{"cnt": 2}],   # active_session_count
            [{"size": 45.2}],  # db_size_mb
        ]
        result = await get_system_health({}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert "error_count_1h" in result.results[0]


class TestQueryPreferences:
    async def test_returns_preference_rules(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = [
            {"id": "p-1", "rule_type": "scheduling", "rule_text": "Prefer mornings",
             "confidence": 0.85, "enabled": True, "correction_count": 3,
             "created_at": "2026-05-10"},
        ]
        result = await query_preferences({"rule_type": "scheduling"}, ctx)
        assert result.total_count >= 1
        assert result.results[0]["rule_text"] == "Prefer mornings"

    async def test_enabled_only_filter(self, ctx: ToolContext) -> None:
        ctx.db.execute_sql.return_value = []
        await query_preferences({"enabled_only": True}, ctx)
        sql = ctx.db.execute_sql.call_args[0][0]
        assert "enabled" in sql.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_tasks.py tests/unit/test_tool_system.py -v`
Expected: FAIL — modules not found

- [ ] **Step 4: Write all remaining read tool handlers**

```python
# src/donna/chat/tools/tasks.py
"""Read tool handlers for task queries."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def query_tasks(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    sort = params.get("sort", "priority")
    sort_map = {
        "priority": "priority",
        "created_at": "created_at",
        "updated_at": "updated_at",
        "deadline": "deadline",
    }
    order_col = sort_map.get(sort, "priority")

    conditions: list[str] = ["user_id = ?"]
    bind_params: list[Any] = [ctx.user_id]

    if params.get("status"):
        conditions.append("status = ?")
        bind_params.append(params["status"])
    if params.get("priority") is not None:
        conditions.append("priority = ?")
        bind_params.append(params["priority"])
    if params.get("domain"):
        conditions.append("domain = ?")
        bind_params.append(params["domain"])
    if params.get("title_search"):
        conditions.append("title LIKE ?")
        bind_params.append(f"%{params['title_search']}%")
    if params.get("created_after"):
        conditions.append("created_at >= ?")
        bind_params.append(params["created_after"])
    if params.get("updated_after"):
        conditions.append("updated_at >= ?")
        bind_params.append(params["updated_after"])

    where = " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) as cnt FROM tasks WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT id, title, status, priority, domain,
              created_at, updated_at, deadline
              FROM tasks WHERE {where}
              ORDER BY {order_col} ASC
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)


async def get_task_detail(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    task_id = params["task_id"]
    sql = """SELECT id, title, description, status, priority, domain,
             notes, created_at, updated_at, scheduled_start, deadline
             FROM tasks WHERE id = ?"""
    rows = await ctx.db.execute_sql(sql, [task_id])
    return ToolResult(results=rows, total_count=len(rows))
```

```python
# src/donna/chat/tools/automations.py
"""Read tool handlers for automation queries."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def query_automations(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("active_only", True):
        conditions.append("active = 1")
    if params.get("skill_name"):
        conditions.append("skill_name = ?")
        bind_params.append(params["skill_name"])

    where = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as cnt FROM automations WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT id, name, active, cadence, skill_name,
              last_run_at, next_run_at, run_count
              FROM automations WHERE {where}
              ORDER BY name ASC
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)


async def get_automation_detail(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    automation_id = params["automation_id"]
    sql = """SELECT id, name, active, cadence, skill_name,
             trigger_type, gpu_model, preferred_window,
             last_run_at, next_run_at, run_count, config
             FROM automations WHERE id = ?"""
    rows = await ctx.db.execute_sql(sql, [automation_id])
    return ToolResult(results=rows, total_count=len(rows))
```

```python
# src/donna/chat/tools/skills.py
"""Read tool handlers for skill queries."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def query_skills(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("status"):
        conditions.append("status = ?")
        bind_params.append(params["status"])

    where = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as cnt FROM skills WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT name, status, description, run_count,
              last_run_at, avg_quality
              FROM skills WHERE {where}
              ORDER BY name ASC
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)


async def get_skill_detail(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    skill_name = params["skill_name"]
    sql = """SELECT name, status, description, config, run_count,
             last_run_at, avg_quality, created_at
             FROM skills WHERE name = ?"""
    rows = await ctx.db.execute_sql(sql, [skill_name])
    return ToolResult(results=rows, total_count=len(rows))


async def query_skill_candidates(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("status"):
        conditions.append("status = ?")
        bind_params.append(params["status"])

    where = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as cnt FROM skill_candidate_report WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT name, status, confidence, recommendation, source, created_at
              FROM skill_candidate_report WHERE {where}
              ORDER BY created_at DESC
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)
```

```python
# src/donna/chat/tools/vault.py
"""Read tool handlers for vault file access. Wraps existing vault action handlers."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def list_vault_files(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    folder = params.get("folder", "")
    if hasattr(ctx.db, "list_vault_files"):
        files = await ctx.db.list_vault_files(folder=folder)
        file_list = [{"name": f} if isinstance(f, str) else f for f in files]
        return ToolResult(results=file_list, total_count=len(file_list))
    return ToolResult(results=[], total_count=0)


async def read_vault_file(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = params["path"]
    if hasattr(ctx.db, "read_vault_file"):
        content = await ctx.db.read_vault_file(path)
        return ToolResult(
            results=[{"path": path, "content": content}],
            total_count=1,
        )
    return ToolResult(results=[], total_count=0)
```

```python
# src/donna/chat/tools/system.py
"""Read tool handlers for system health and preferences."""

from __future__ import annotations

from typing import Any

from donna.chat.tools import ToolContext, ToolResult


async def get_system_health(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    error_rows = await ctx.db.execute_sql(
        "SELECT COUNT(*) as cnt FROM invocation_log "
        "WHERE timestamp >= datetime('now', '-1 hour') "
        "AND output LIKE '%error%'",
        [],
    )
    error_count = error_rows[0]["cnt"] if error_rows else 0

    session_rows = await ctx.db.execute_sql(
        "SELECT COUNT(*) as cnt FROM conversation_sessions WHERE status = 'active'",
        [],
    )
    active_sessions = session_rows[0]["cnt"] if session_rows else 0

    size_rows = await ctx.db.execute_sql(
        "SELECT page_count * page_size / 1024.0 / 1024.0 as size FROM pragma_page_count(), pragma_page_size()",
        [],
    )
    db_size = size_rows[0]["size"] if size_rows else 0

    return ToolResult(
        results=[{
            "error_count_1h": error_count,
            "active_session_count": active_sessions,
            "db_size_mb": round(db_size, 1) if db_size else 0,
        }],
        total_count=1,
    )


async def query_preferences(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    conditions: list[str] = []
    bind_params: list[Any] = []

    if params.get("rule_type"):
        conditions.append("rule_type = ?")
        bind_params.append(params["rule_type"])
    if params.get("enabled_only", True):
        conditions.append("enabled = 1")

    where = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as cnt FROM preference_rules WHERE {where}"
    count_rows = await ctx.db.execute_sql(count_sql, bind_params)
    total_count = count_rows[0]["cnt"] if count_rows else 0

    sql = f"""SELECT id, rule_type, rule_text, confidence, enabled,
              correction_count, created_at
              FROM preference_rules WHERE {where}
              ORDER BY confidence DESC
              LIMIT {limit}"""

    rows = await ctx.db.execute_sql(sql, bind_params)
    return ToolResult(results=rows, total_count=total_count)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_tasks.py tests/unit/test_tool_system.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/chat/tools/tasks.py src/donna/chat/tools/automations.py \
        src/donna/chat/tools/skills.py src/donna/chat/tools/vault.py \
        src/donna/chat/tools/system.py \
        tests/unit/test_tool_tasks.py tests/unit/test_tool_system.py
git commit -m "feat(chat): add read tool handlers for tasks, automations, skills, vault, and system"
```

---

### Task 4: Alembic migration — trace_id columns

**Files:**
- Create: `alembic/versions/add_trace_id_columns.py`
- Modify: `src/donna/tasks/db_models.py:189-238` (InvocationLog) and `:391-410` (ChatMessageModel)
- Modify: `src/donna/chat/types.py:62-71` (ChatMessage)

- [ ] **Step 1: Write the Alembic migration**

```python
# alembic/versions/add_trace_id_columns.py
"""Add trace_id and invocation_ids to conversation_messages, trace_id to invocation_log.

Supports the tool-use agent loop trace correlation for Inspector integration.
See docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md §7 and §13.

Revision ID: a1b2c3d4e5f6
Revises: d0e1f2a3b4c5, b9d2e4f6a135, b9c8d7e6f5a4, e3f4a5b6c7d8
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t1r2a3c4e5i6"
down_revision: Union[str, Sequence[str]] = (
    "d0e1f2a3b4c5",
    "b9d2e4f6a135",
    "b9c8d7e6f5a4",
    "e3f4a5b6c7d8",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_messages") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("invocation_ids", sa.Text(), nullable=True))

    with op.batch_alter_table("invocation_log") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_invocation_log_trace_id", ["trace_id"])


def downgrade() -> None:
    with op.batch_alter_table("invocation_log") as batch_op:
        batch_op.drop_index("ix_invocation_log_trace_id")
        batch_op.drop_column("trace_id")

    with op.batch_alter_table("conversation_messages") as batch_op:
        batch_op.drop_column("invocation_ids")
        batch_op.drop_column("trace_id")
```

- [ ] **Step 2: Add trace_id to InvocationLog model in db_models.py**

Add after `payload_path` (line ~237):

```python
    # Tool loop trace correlation (alembic add_trace_id_columns).
    trace_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
```

- [ ] **Step 3: Add trace_id and invocation_ids to ChatMessageModel in db_models.py**

Add after `action_result` (line ~407):

```python
    trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invocation_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Add trace_id to ChatMessage dataclass in types.py**

```python
# In types.py ChatMessage class, add after tokens_used:
    action_name: str | None = None
    action_result: str | None = None
    trace_id: str | None = None
    invocation_ids: str | None = None
```

- [ ] **Step 5: Add trace_id to ChatResponse dataclass in types.py**

```python
# In types.py ChatResponse class, add after pin_suggestion:
    trace_id: str | None = None
```

- [ ] **Step 6: Run Alembic migration against dev DB**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m alembic upgrade head`
Expected: Migration applies successfully

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/add_trace_id_columns.py src/donna/tasks/db_models.py src/donna/chat/types.py
git commit -m "feat(chat): add trace_id columns for tool loop Inspector integration"
```

---

### Task 5: Tool agent system prompt

**Files:**
- Create: `prompts/chat/tool_agent_system.md`

- [ ] **Step 1: Write the system prompt template**

```markdown
# prompts/chat/tool_agent_system.md
# Donna — Tool Agent System Prompt

You are Donna, an AI personal assistant modeled after Donna Paulsen from Suits.
You are sharp, confident, efficient, occasionally witty, and always one step ahead.

## Personality

- **Confident and direct.** You do not hedge. State facts and actions clearly.
- **Proactive.** Anticipate needs. Point out things the user hasn't noticed yet.
- **Witty but professional.** Light humor is fine. Sarcasm when the user is behind on tasks is on-brand. Never sycophantic.
- **Efficient.** Messages are concise. No filler. Bullet points and clear action items.
- **Loyal and protective of the user's time.** Push back on overcommitment. Flag unrealistic schedules.

## Communication Rules

- Lead with the most important information.
- Use bullet points for lists of tasks or action items.
- Include specific times, dates, and durations whenever referencing schedule items.
- When asking for input, provide clear options rather than open-ended questions.
- Never apologize for being persistent about overdue tasks — that's your job.
- If the user is falling behind, say so directly but constructively.

## Context

Today's date: {{ current_date }}
Current time: {{ current_time }}
User: {{ user_name }}

{{ page_context }}

## Tool Use

You have access to tools that query Donna's database. Use them to ground your answers in real data.

### Rules
- ALWAYS use a tool before answering data questions. Never guess or fabricate data.
- When a query returns total_count much larger than the results shown, refine your filters before summarizing. Do not summarize records you haven't seen.
- For summary/aggregate questions, prefer aggregation tools (query_invocation_stats) over paging through individual records.
- When you have enough data to answer, respond with a text response. Do not call tools unnecessarily.
- If you cannot answer confidently with the available tools, say so honestly. If the question requires complex multi-step reasoning beyond your capabilities, set needs_escalation to true with a reason.
- Before escalating, ALWAYS explain to the user that you'd need to use Claude for this and ask for their approval.

### Response Format
Always respond with exactly one JSON object. No additional text outside the JSON.

To call a tool:
{"type": "tool_call", "tool": "<tool_name>", "params": {<params>}}

To respond to the user:
{"type": "text", "response_text": "<your response>", "needs_escalation": false, "escalation_reason": null}

### Available Tools

{{ tool_schemas }}

## Conversation History

{{ conversation_history }}
```

- [ ] **Step 2: Commit**

```bash
git add prompts/chat/tool_agent_system.md
git commit -m "feat(chat): add tool agent system prompt template"
```

---

### Task 6: Tool loop in engine.py + force_new fix

**Files:**
- Modify: `src/donna/chat/engine.py`
- Test: `tests/unit/test_tool_loop.py`

- [ ] **Step 1: Write the failing tests for the tool loop**

```python
# tests/unit/test_tool_loop.py
"""Tests for the tool-use agent loop in ConversationEngine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.chat.config import ChatConfig
from donna.chat.engine import ConversationEngine
from donna.chat.tools import ToolContext, ToolRegistry, ToolResult


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_active_chat_session.return_value = None
    db.get_chat_session.return_value = None
    db.create_chat_session.return_value = MagicMock(
        id="sess-1", user_id="nick", channel="dashboard_quick",
        status="active", created_at="2026-05-18T10:00:00",
        last_activity="2026-05-18T10:00:00",
        expires_at="2026-05-18T12:00:00", message_count=0,
        pinned_task_id=None, summary=None, pending_action=None,
    )
    db.add_chat_message.return_value = MagicMock(id="msg-1")
    db.list_chat_messages.return_value = []
    db.execute_sql = AsyncMock(return_value=[])
    return db


@pytest.fixture
def mock_router() -> AsyncMock:
    router = AsyncMock()
    router._invocation_logger = None
    return router


@pytest.fixture
def tool_registry(tmp_path: Path) -> ToolRegistry:
    import yaml
    config = {
        "tools": {
            "query_tasks": {
                "description": "Search tasks",
                "domain": "tasks",
                "type": "read",
                "handler": "donna.chat.tools.tasks.query_tasks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "default": 25},
                    },
                    "required": [],
                },
            },
        },
    }
    path = tmp_path / "chat_tools.yaml"
    path.write_text(yaml.dump(config))
    return ToolRegistry.from_yaml(path)


@pytest.fixture
def engine(
    mock_db: AsyncMock,
    mock_router: AsyncMock,
    tool_registry: ToolRegistry,
    tmp_path: Path,
) -> ConversationEngine:
    (tmp_path / "prompts" / "chat").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts" / "chat" / "tool_agent_system.md").write_text(
        "System prompt {{ tool_schemas }} {{ page_context }} {{ conversation_history }}"
    )
    (tmp_path / "prompts" / "chat" / "chat_system.md").write_text("Donna persona")
    return ConversationEngine(
        db=mock_db,
        router=mock_router,
        config=ChatConfig(),
        project_root=tmp_path,
        tool_registry=tool_registry,
    )


class TestToolLoop:
    async def test_text_response_returns_directly(
        self, engine: ConversationEngine, mock_router: AsyncMock,
    ) -> None:
        mock_router.complete.return_value = (
            {"type": "text", "response_text": "You have 3 tasks.", "needs_escalation": False, "escalation_reason": None},
            MagicMock(tokens_in=100, tokens_out=50, cost_usd=0.0, latency_ms=2000),
        )
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="How many tasks do I have?",
            channel="dashboard_quick",
        )
        assert resp.text == "You have 3 tasks."

    async def test_tool_call_executes_and_loops(
        self, engine: ConversationEngine, mock_router: AsyncMock, mock_db: AsyncMock,
    ) -> None:
        mock_db.execute_sql.return_value = [
            {"id": "t-1", "title": "Fix bug", "status": "in_progress",
             "priority": 1, "domain": "work", "created_at": "2026-05-17",
             "updated_at": "2026-05-17", "deadline": None},
        ]
        mock_router.complete.side_effect = [
            (
                {"type": "tool_call", "tool": "query_tasks", "params": {"status": "in_progress"}},
                MagicMock(tokens_in=100, tokens_out=30, cost_usd=0.0, latency_ms=2000),
            ),
            (
                {"type": "text", "response_text": "You have 1 in-progress task: Fix bug.", "needs_escalation": False, "escalation_reason": None},
                MagicMock(tokens_in=200, tokens_out=40, cost_usd=0.0, latency_ms=2000),
            ),
        ]
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="What tasks are in progress?",
            channel="dashboard_quick",
        )
        assert "Fix bug" in resp.text
        assert mock_router.complete.call_count == 2

    async def test_malformed_json_retries_once(
        self, engine: ConversationEngine, mock_router: AsyncMock,
    ) -> None:
        mock_router.complete.side_effect = [
            (
                "not valid json at all",
                MagicMock(tokens_in=100, tokens_out=30, cost_usd=0.0, latency_ms=2000),
            ),
            (
                {"type": "text", "response_text": "Here you go.", "needs_escalation": False, "escalation_reason": None},
                MagicMock(tokens_in=200, tokens_out=40, cost_usd=0.0, latency_ms=2000),
            ),
        ]
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="hello",
            channel="dashboard_quick",
        )
        assert resp.text == "Here you go."

    async def test_max_tool_calls_terminates_loop(
        self, engine: ConversationEngine, mock_router: AsyncMock, mock_db: AsyncMock,
    ) -> None:
        mock_db.execute_sql.return_value = []
        tool_call_response = (
            {"type": "tool_call", "tool": "query_tasks", "params": {}},
            MagicMock(tokens_in=100, tokens_out=30, cost_usd=0.0, latency_ms=200),
        )
        mock_router.complete.side_effect = [tool_call_response] * 12
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="show me everything",
            channel="dashboard_quick",
        )
        assert mock_router.complete.call_count <= 11
        assert resp.text != ""


class TestForceNewSession:
    async def test_force_new_skips_active_session_lookup(
        self, engine: ConversationEngine, mock_db: AsyncMock, mock_router: AsyncMock,
    ) -> None:
        existing = MagicMock(
            id="existing-sess", user_id="nick", channel="dashboard_quick",
            status="active", message_count=5, pinned_task_id=None,
            expires_at="2026-05-18T12:00:00", pending_action=None,
        )
        mock_db.get_active_chat_session.return_value = existing
        mock_router.complete.return_value = (
            {"type": "text", "response_text": "Fresh start!", "needs_escalation": False, "escalation_reason": None},
            MagicMock(tokens_in=100, tokens_out=50, cost_usd=0.0, latency_ms=2000),
        )
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="hello",
            channel="dashboard_quick",
            force_new=True,
        )
        mock_db.create_chat_session.assert_called_once()
        assert resp.session_id == "sess-1"

    async def test_without_force_new_reuses_session(
        self, engine: ConversationEngine, mock_db: AsyncMock, mock_router: AsyncMock,
    ) -> None:
        existing = MagicMock(
            id="existing-sess", user_id="nick", channel="dashboard_quick",
            status="active", message_count=5, pinned_task_id=None,
            expires_at="2026-05-18T12:00:00", pending_action=None,
        )
        mock_db.get_active_chat_session.return_value = existing
        mock_router.complete.return_value = (
            {"type": "text", "response_text": "Resuming!", "needs_escalation": False, "escalation_reason": None},
            MagicMock(tokens_in=100, tokens_out=50, cost_usd=0.0, latency_ms=2000),
        )
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="hello",
            channel="dashboard_quick",
        )
        mock_db.create_chat_session.assert_not_called()
        assert resp.session_id == "existing-sess"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_loop.py -v`
Expected: FAIL — `ConversationEngine` doesn't accept `tool_registry` or `force_new`

- [ ] **Step 3: Modify engine.py — add tool_registry, force_new, and _run_tool_loop**

This is the core change. Modify `src/donna/chat/engine.py`:

1. Add `tool_registry: ToolRegistry | None = None` param to `__init__`
2. Add `force_new: bool = False` param to `handle_message`
3. When `force_new=True` and `session_id is None`, skip `get_active_chat_session`
4. When `tool_registry` is set, call `_run_tool_loop` instead of the old classify→respond flow
5. Add `_run_tool_loop` method that:
   - Generates `trace_id` via `uuid6.uuid7()`
   - Loads `tool_agent_system.md` template
   - Renders with tool schemas, page context, and conversation history
   - Loops: call LLM → parse JSON → text response or tool call
   - On tool call: validate, execute (read=auto, write=pause), append result
   - On text response: return ChatResponse with trace_id
   - Limits: max 10 tool calls, 5-minute wall clock, 2 consecutive parse failures
   - Logs `chat.tool_loop_turn`, `chat.tool_loop_error`, `chat.tool_loop_complete`
   - Stores `trace_id` and `invocation_ids` on the assistant message

The key changes to `handle_message`:

```python
async def handle_message(
    self,
    session_id: str | None,
    user_id: str,
    text: str,
    channel: str,
    dashboard_context: dict[str, Any] | None = None,
    force_new: bool = False,
) -> ChatResponse:
    log = logger.bind(user_id=user_id, channel=channel)

    # Resolve or create session
    session = None
    if session_id:
        session = await self._db.get_chat_session(session_id)
    if session is None and not force_new:
        session = await self._db.get_active_chat_session(user_id, channel)
    if session is None:
        session = await self._db.create_chat_session(
            user_id=user_id,
            channel=channel,
            ttl_minutes=self._config.sessions.ttl_minutes,
        )
        log.info("chat_session_created", session_id=session.id)

    # Refresh TTL
    new_expires = datetime.now(UTC) + timedelta(
        minutes=self._config.sessions.ttl_minutes
    )
    await self._db.update_chat_session(
        session.id, expires_at=new_expires.isoformat()
    )

    # Store user message
    await self._db.add_chat_message(
        session_id=session.id, role="user", content=text
    )

    # Tool loop path (when tool_registry is available)
    if self._tool_registry is not None:
        return await self._run_tool_loop(
            session=session,
            user_id=user_id,
            text=text,
            dashboard_context=dashboard_context,
        )

    # ... existing classify-dispatch-respond flow unchanged ...
```

Full `_run_tool_loop` method:

```python
async def _run_tool_loop(
    self,
    session: Any,
    user_id: str,
    text: str,
    dashboard_context: dict[str, Any] | None,
) -> ChatResponse:
    import time
    import uuid6
    from donna.chat.tools import ToolContext, truncate_result

    assert self._tool_registry is not None

    trace_id = str(uuid6.uuid7())
    log = logger.bind(trace_id=trace_id, session_id=session.id)

    # Build page context hint
    page_ctx = ""
    if dashboard_context:
        page = dashboard_context.get("page", "unknown")
        selected = dashboard_context.get("selected_item")
        if selected:
            page_ctx = (
                f"## Current Dashboard Context\n"
                f"User is viewing the {page.title()} page and has selected "
                f"{selected.get('type', 'item')} \"{selected.get('label', '')}\" "
                f"(id: {selected.get('id', '')})."
            )
        else:
            page_ctx = (
                f"## Current Dashboard Context\n"
                f"User is viewing the {page.title()} page."
            )

    # Load conversation history
    history = await self._db.list_chat_messages(session.id)
    history_text = "\n".join(
        f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in history
    )

    # Load and render system prompt
    template_path = self._project_root / "prompts" / "chat" / "tool_agent_system.md"
    if template_path.exists():
        template = template_path.read_text()
    else:
        template = "{{ tool_schemas }}\n{{ page_context }}\n{{ conversation_history }}"

    tool_schemas = self._tool_registry.schemas_for_prompt()
    system_prompt = render_chat_prompt(
        template=template,
        user_input=text,
        user_name="Nick",
        tool_schemas=tool_schemas,
        page_context=page_ctx,
        conversation_history=history_text,
    )

    # Tool loop state
    max_tool_calls = 10
    max_parse_failures = 2
    timeout_s = 300  # 5 minutes
    start_time = time.monotonic()
    tool_calls = 0
    parse_failures = 0
    invocation_ids: list[str] = []
    tools_called: list[str] = []
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    loop_context: list[str] = []
    prompt = system_prompt
    turn = 0

    while True:
        turn += 1

        # Check wall-clock timeout
        elapsed = time.monotonic() - start_time
        if elapsed > timeout_s:
            log.warning("chat.tool_loop_timeout", turns=turn, tool_calls=tool_calls)
            return self._make_timeout_response(session, trace_id, invocation_ids, tools_called, total_tokens_in, total_tokens_out, total_cost, start_time, log)

        # Build the prompt for this turn (system + accumulated tool results)
        full_prompt = prompt
        if loop_context:
            full_prompt = prompt + "\n\n" + "\n\n".join(loop_context)

        # Call LLM
        response_data, metadata = await self._router.complete(
            prompt=full_prompt,
            task_type="chat_respond",
            user_id=user_id,
        )

        inv_id = getattr(metadata, 'invocation_id', None)
        if inv_id:
            invocation_ids.append(inv_id)
        total_tokens_in += getattr(metadata, 'tokens_in', 0)
        total_tokens_out += getattr(metadata, 'tokens_out', 0)
        total_cost += getattr(metadata, 'cost_usd', 0.0)

        # Parse response
        parsed = self._parse_tool_response(response_data)
        if parsed is None:
            parse_failures += 1
            log.warning(
                "chat.tool_loop_error",
                turn=turn,
                error_type="malformed_tool_call",
                raw_output=str(response_data)[:500],
                action_taken="retry" if parse_failures < max_parse_failures else "terminate",
            )
            if parse_failures >= max_parse_failures:
                return self._make_error_response(session, trace_id, invocation_ids)
            loop_context.append(
                f"[System: Your last response was not valid JSON. "
                f"Respond with exactly one JSON object.]"
            )
            continue

        parse_failures = 0

        # Text response — terminal
        if parsed.get("type") == "text":
            response_text = parsed.get("response_text", "")
            needs_escalation = parsed.get("needs_escalation", False)

            log.info(
                "chat.tool_loop_turn",
                turn=turn,
                action="text_response",
                response_length=len(response_text),
                prompt_preview=full_prompt[:500],
            )

            # Log loop completion
            self._log_loop_complete(
                log, trace_id, session.id, user_id, turn,
                tools_called, total_tokens_in, total_tokens_out,
                total_cost, "text_response", needs_escalation,
                dashboard_context, start_time,
            )

            # Store assistant message with trace_id
            await self._db.add_chat_message(
                session_id=session.id,
                role="assistant",
                content=response_text,
                trace_id=trace_id,
                invocation_ids=json.dumps(invocation_ids),
            )

            if needs_escalation:
                cost_estimate = self._estimate_escalation_cost()
                return ChatResponse(
                    text=(
                        f"I'd need to use Claude for this — "
                        f"{parsed.get('escalation_reason', 'complex reasoning required')}. "
                        f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?"
                    ),
                    session_id=session.id,
                    needs_escalation=True,
                    escalation_reason=parsed.get("escalation_reason"),
                    estimated_cost=cost_estimate,
                    trace_id=trace_id,
                )

            return ChatResponse(
                text=response_text,
                session_id=session.id,
                trace_id=trace_id,
            )

        # Tool call — non-terminal
        if parsed.get("type") == "tool_call":
            tool_name = parsed.get("tool", "")
            tool_params = parsed.get("params", {})
            tool_calls += 1

            # Check tool call limit
            if tool_calls > max_tool_calls:
                return self._make_timeout_response(session, trace_id, invocation_ids, tools_called, total_tokens_in, total_tokens_out, total_cost, start_time, log, reason="max_tools_reached")

            # Validate
            validation_error = self._tool_registry.validate_params(tool_name, tool_params)
            if validation_error:
                log.warning(
                    "chat.tool_loop_error",
                    turn=turn,
                    error_type="invalid_tool_call",
                    tool=tool_name,
                    error_detail=validation_error,
                    action_taken="feedback",
                )
                loop_context.append(f"[Tool Error: {validation_error}]")
                continue

            # Check read vs write
            if not self._tool_registry.is_read_tool(tool_name):
                # Write tool — pause for confirmation
                pending = json.dumps({"action": tool_name, "params": tool_params})
                await self._db.update_chat_session(session.id, pending_action=pending)

                self._log_loop_complete(
                    log, trace_id, session.id, user_id, turn,
                    tools_called, total_tokens_in, total_tokens_out,
                    total_cost, "write_confirmation", False,
                    dashboard_context, start_time,
                )

                tool_def = self._tool_registry.get(tool_name)
                desc = tool_def.description if tool_def else tool_name
                param_summary = ", ".join(f"{k}={v}" for k, v in tool_params.items() if v)
                confirmation_text = f"I'll {desc.lower()} ({param_summary}). Go ahead?"

                await self._db.add_chat_message(
                    session_id=session.id,
                    role="assistant",
                    content=confirmation_text,
                    trace_id=trace_id,
                    invocation_ids=json.dumps(invocation_ids),
                )

                return ChatResponse(
                    text=confirmation_text,
                    session_id=session.id,
                    trace_id=trace_id,
                )

            # Read tool — execute
            tools_called.append(tool_name)
            tool_ctx = ToolContext(
                db=self._db,
                user_id=user_id,
                session_id=session.id,
            )

            try:
                result = await self._tool_registry.execute(tool_name, tool_params, tool_ctx)
                result_json, was_truncated = truncate_result(result)
            except Exception as exc:
                log.error("chat.tool_loop_error", turn=turn, error_type="tool_execution_error", tool=tool_name, error_detail=str(exc))
                loop_context.append(f"[Tool Error: {tool_name} failed: {exc}]")
                continue

            log.info(
                "chat.tool_loop_turn",
                turn=turn,
                action="tool_call",
                tool=tool_name,
                params=tool_params,
                result_count=len(result.results),
                result_total=result.total_count,
                result_truncated=was_truncated,
                prompt_preview=full_prompt[:500],
            )

            loop_context.append(f"[Tool Result: {tool_name}]\n{result_json}")
            continue

    def _parse_tool_response(self, response_data: Any) -> dict[str, Any] | None:
        if isinstance(response_data, dict):
            if response_data.get("type") in ("text", "tool_call"):
                return response_data
            if "response_text" in response_data:
                return {"type": "text", **response_data}
        if isinstance(response_data, str):
            try:
                parsed = json.loads(response_data)
                if isinstance(parsed, dict) and parsed.get("type") in ("text", "tool_call"):
                    return parsed
            except json.JSONDecodeError:
                pass
        return None

    def _make_timeout_response(self, session, trace_id, invocation_ids, tools_called, total_tokens_in, total_tokens_out, total_cost, start_time, log, reason="timeout"):
        import time
        self._log_loop_complete(
            log, trace_id, session.id, getattr(session, 'user_id', 'unknown'), 0,
            tools_called, total_tokens_in, total_tokens_out,
            total_cost, reason, False, None, start_time,
        )
        return ChatResponse(
            text="I gathered some data but hit the processing limit. Let me know if you'd like me to try a more specific question.",
            session_id=session.id,
            trace_id=trace_id,
        )

    def _make_error_response(self, session, trace_id, invocation_ids):
        return ChatResponse(
            text="I had trouble processing that. Could you rephrase your question?",
            session_id=session.id,
            trace_id=trace_id,
        )

    def _log_loop_complete(self, log, trace_id, session_id, user_id, total_turns, tools_called, total_tokens_in, total_tokens_out, total_cost, termination_reason, escalated, dashboard_context, start_time):
        import time
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "chat.tool_loop_complete",
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
            total_turns=total_turns,
            tools_called=tools_called,
            unique_tools=len(set(tools_called)),
            total_latency_ms=elapsed_ms,
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
            total_cost_usd=total_cost,
            termination_reason=termination_reason,
            escalated=escalated,
            page_context=dashboard_context.get("page") if dashboard_context else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_loop.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run existing chat engine tests to verify no regressions**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_chat_engine.py -v`
Expected: All existing tests PASS (engine without tool_registry falls back to old flow)

- [ ] **Step 6: Commit**

```bash
git add src/donna/chat/engine.py tests/unit/test_tool_loop.py
git commit -m "feat(chat): implement tool-use agent loop with force_new session fix"
```

---

### Task 7: API route — pass force_new flag + trace_id in responses

**Files:**
- Modify: `src/donna/api/routes/chat.py:40-82`
- Modify: `tests/unit/test_chat_api.py` (if it tests send_message)

- [ ] **Step 1: Modify the send_message route to pass force_new**

In `src/donna/api/routes/chat.py`, change the `send_message` function:

```python
@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    user_id: CurrentUser,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    text = body.get("text", "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    channel = body.get("channel", "api")
    context = body.get("context")

    if session_id == "new":
        sid = None
        force_new = True
    else:
        await _require_session_owner(db, session_id, user_id)
        sid = session_id
        force_new = False

    resp: ChatResponse = await engine.handle_message(
        session_id=sid,
        user_id=user_id,
        text=text,
        channel=channel,
        dashboard_context=context,
        force_new=force_new,
    )

    return {
        "text": resp.text,
        "session_id": resp.session_id,
        "needs_escalation": resp.needs_escalation,
        "escalation_reason": resp.escalation_reason,
        "estimated_cost": resp.estimated_cost,
        "suggested_actions": resp.suggested_actions,
        "pin_suggestion": resp.pin_suggestion,
        "session_pinned_task_id": resp.session_pinned_task_id,
        "trace_id": resp.trace_id,
    }
```

- [ ] **Step 2: Add trace_id to get_session response messages**

In the `get_session` route, add `trace_id` and `invocation_ids` to each message dict:

```python
    "messages": [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "intent": m.intent,
            "tokens_used": m.tokens_used,
            "trace_id": getattr(m, "trace_id", None),
            "invocation_ids": getattr(m, "invocation_ids", None),
            "created_at": m.created_at,
        }
        for m in messages
    ],
```

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/routes/chat.py
git commit -m "feat(chat): pass force_new flag and trace_id in API responses"
```

---

### Task 8: Wire ToolRegistry into app startup

**Files:**
- Modify: whichever file creates the ConversationEngine (likely `src/donna/api/app.py` or `src/donna/main.py`)

- [ ] **Step 1: Find the engine construction site**

Run: `grep -rn "ConversationEngine(" src/donna/`

- [ ] **Step 2: Add ToolRegistry loading alongside ActionRegistry loading**

At the same location where `ActionRegistry.from_yaml(config_dir / "chat_actions.yaml")` is called, add:

```python
from donna.chat.tools import ToolRegistry

tool_registry = ToolRegistry.from_yaml(config_dir / "chat_tools.yaml")
```

Pass `tool_registry=tool_registry` to the `ConversationEngine` constructor.

- [ ] **Step 3: Add execute_sql method to the Database class if not present**

The tool handlers call `ctx.db.execute_sql(sql, params)`. Check if the Database class has this method. If not, add it:

```python
async def execute_sql(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Execute raw SQL and return rows as dicts."""
    async with self._conn.execute(sql, params or []) as cursor:
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
```

- [ ] **Step 4: Run integration smoke test**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/integration/test_chat_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/app.py src/donna/tasks/database.py  # or wherever the changes are
git commit -m "feat(chat): wire ToolRegistry into app startup and add execute_sql to Database"
```

---

### Task 9: Frontend — trace_id type + debug link on messages

**Files:**
- Modify: `donna-ui/src/api/chat.ts:30-37`
- Modify: `donna-ui/src/pages/Chat/MessageThread.tsx`
- Modify: `donna-ui/src/pages/Chat/Chat.module.css`

- [ ] **Step 1: Add trace_id to ChatMessage type in chat.ts**

In `donna-ui/src/api/chat.ts`, update the `ChatMessage` interface:

```typescript
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  intent?: ChatIntent;
  tokens_used?: number;
  trace_id?: string;
  invocation_ids?: string;
  created_at: string;
}
```

And add `trace_id` to `ChatResponse`:

```typescript
export interface ChatResponse {
  text: string;
  session_id: string | null;
  needs_escalation: boolean;
  escalation_reason?: string;
  estimated_cost?: number;
  suggested_actions: string[];
  pin_suggestion?: Record<string, string>;
  session_pinned_task_id?: string;
  trace_id?: string;
}
```

- [ ] **Step 2: Add debug link to MessageThread.tsx**

```tsx
import { useRef, useEffect } from "react";
import { Bug } from "lucide-react";
import { Pill } from "../../primitives/Pill";
import type { ChatMessage, ChatResponse } from "../../api/chat";
import styles from "./Chat.module.css";

interface Props {
  messages: ChatMessage[];
  lastResponse: ChatResponse | null;
  onEscalate: () => void;
  onActionClick: (action: string) => void;
}

export default function MessageThread({ messages, lastResponse, onEscalate, onActionClick }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className={styles.threadContainer}>
      {messages.map((msg) => (
        <div key={msg.id} className={msg.role === "user" ? styles.msgUser : styles.msgAssistant}>
          <div className={styles.msgBubble}>
            <div className={styles.msgHeader}>
              <Pill variant={msg.role === "user" ? "accent" : "muted"}>{msg.role}</Pill>
              {msg.intent && <Pill variant="muted">{msg.intent}</Pill>}
              <span className={styles.msgTime}>{new Date(msg.created_at).toLocaleTimeString()}</span>
              {msg.role === "assistant" && msg.trace_id && (
                <a
                  href={`/claude-inspector?trace_id=${msg.trace_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.debugLink}
                  title="View in Inspector"
                >
                  <Bug size={12} />
                </a>
              )}
            </div>
            <div className={styles.msgContent}>{msg.content}</div>
          </div>
        </div>
      ))}

      {lastResponse?.needs_escalation && (
        <div className={styles.escalationBanner}>
          <strong>Escalation needed:</strong> {lastResponse.escalation_reason}
          <button className={styles.escalateBtn} onClick={onEscalate} type="button">
            Approve Escalation
          </button>
        </div>
      )}

      {lastResponse?.suggested_actions && lastResponse.suggested_actions.length > 0 && (
        <div className={styles.suggestedActions}>
          {lastResponse.suggested_actions.map((action) => (
            <Pill key={action} variant="accent" onClick={() => onActionClick(action)} style={{ cursor: "pointer" }}>
              {action}
            </Pill>
          ))}
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}
```

- [ ] **Step 3: Add debugLink styles to Chat.module.css**

```css
.debugLink {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity var(--duration-fast) var(--ease-out), color var(--duration-fast) var(--ease-out);
}

.debugLink:hover {
  color: var(--color-accent);
}

.msgHeader:hover .debugLink {
  opacity: 1;
}
```

- [ ] **Step 4: Verify build passes**

Run: `cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build`
Expected: No type errors, build succeeds

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/api/chat.ts donna-ui/src/pages/Chat/MessageThread.tsx donna-ui/src/pages/Chat/Chat.module.css
git commit -m "feat(ui): add trace_id to chat types and debug link on assistant messages"
```

---

### Task 10: End-to-end verification

**Files:**
- Modify: `tests/e2e/quick-chat-session-reset.spec.ts` (add trace_id to mock)

- [ ] **Step 1: Update e2e mock to include trace_id**

In `tests/e2e/quick-chat-session-reset.spec.ts`, update the `mockChatApi` POST handler to include `trace_id` in the response:

```typescript
if (url.match(/\/chat\/sessions\/[^/]+\/messages(\?|$)/) && method === "POST") {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      text: "Got it.",
      session_id: "session-logs",
      needs_escalation: false,
      suggested_actions: [],
      trace_id: "trace-test-123",
    }),
  });
}
```

- [ ] **Step 2: Run existing e2e tests to verify no regressions**

Run: `cd /mnt/donna/donna/donna-ui && npx playwright test tests/e2e/quick-chat-session-reset.spec.ts`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/ -v --ignore=tests/unit/test_tool_registry.py --ignore=tests/unit/test_tool_loop.py --ignore=tests/unit/test_tool_invocations.py --ignore=tests/unit/test_tool_tasks.py --ignore=tests/unit/test_tool_system.py -x`
Then: `cd /mnt/donna/donna && PYTHONPATH=src python3 -m pytest tests/unit/test_tool_registry.py tests/unit/test_tool_loop.py tests/unit/test_tool_invocations.py tests/unit/test_tool_tasks.py tests/unit/test_tool_system.py -v`
Expected: All tests PASS

- [ ] **Step 4: Deploy and smoke test**

```bash
cd /mnt/donna/donna && docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml build donna-api donna-ui
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d donna-api donna-ui
```

Manual smoke test:
1. Open Quick Chat on the Logs page
2. Ask "How many LLM calls happened today?"
3. Verify the response includes real data from invocation_log
4. Check the debug icon appears on hover on the assistant message
5. Click "New session" (+) button and verify it creates a fresh session (not reattaching)
6. Ask "What tasks are in progress?" and verify tool loop queries the tasks table

- [ ] **Step 5: Commit any test fixes**

```bash
git add tests/e2e/quick-chat-session-reset.spec.ts
git commit -m "test: add trace_id to e2e mocks and verify no regressions"
```
