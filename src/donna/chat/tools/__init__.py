"""Tool registry for the chat tool-use agent loop.

Loads tool schemas from config/chat_tools.yaml, validates parameters,
resolves handlers, and executes tool calls.

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
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

    db: Any  # donna.tasks.database.Database
    user_id: str
    session_id: str


@dataclasses.dataclass(frozen=True)
class ToolDefinition:
    """Single tool from chat_tools.yaml."""

    name: str
    description: str
    domain: str
    tool_type: str  # "read" | "write"
    handler: str  # dotted path, e.g. "donna.chat.tools.tasks.query_tasks"
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
        """Load tool definitions from a YAML config file.

        Args:
            path: Path to the YAML config file.

        Returns:
            A ToolRegistry populated with definitions, or an empty registry
            if the file does not exist.
        """
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
        """Return a tool definition by name, or None if not found."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        return list(self._tools.values())

    def is_read_tool(self, name: str) -> bool:
        """Return True if the named tool is a read tool.

        Args:
            name: Tool name to check.

        Returns:
            True if the tool exists and has type "read", False otherwise.
        """
        tool = self._tools.get(name)
        return tool is not None and tool.tool_type == "read"

    def schemas_for_prompt(self) -> str:
        """Format all tool schemas as readable text for the LLM system prompt.

        Returns:
            A multi-line string listing each tool with its description,
            type, and parameter details.
        """
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
        """Validate params against the tool schema.

        Args:
            tool_name: Name of the tool to validate against.
            params: Parameter dict to validate.

        Returns:
            An error string describing the first validation failure,
            or None if the params are valid.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return f"Unknown tool: {tool_name}"
        required = tool.parameters.get("required", [])
        missing = [r for r in required if r not in params or params[r] is None]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None

    def _resolve_handler(self, tool: ToolDefinition) -> ToolHandler:
        """Lazily import and cache the handler function for a tool.

        Args:
            tool: The ToolDefinition whose handler to resolve.

        Returns:
            The callable handler function.

        Raises:
            ImportError: If the module cannot be imported.
            AttributeError: If the function is not found in the module.
        """
        if tool.name in self._handlers:
            return self._handlers[tool.name]
        module_path, func_name = tool.handler.rsplit(".", 1)
        module = importlib.import_module(module_path)
        handler: ToolHandler = getattr(module, func_name)
        self._handlers[tool.name] = handler
        return handler

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Execute a tool by name and return its result.

        Args:
            tool_name: Name of the tool to execute.
            params: Parameters to pass to the handler.
            ctx: Execution context with db and user info.

        Returns:
            ToolResult with results list and metadata. Returns an empty
            ToolResult if the tool name is unknown.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            logger.warning("tool_not_found", tool_name=tool_name)
            return ToolResult(results=[], total_count=0)
        try:
            handler = self._resolve_handler(tool)
            return await handler(params, ctx)
        except Exception as exc:
            logger.error(
                "tool_execution_failed",
                tool=tool_name,
                error=str(exc),
            )
            return ToolResult(results=[], total_count=0)


def truncate_result(result: ToolResult) -> tuple[str, bool]:
    """Serialize a ToolResult to JSON, truncating if over the token budget.

    Estimates token count as len(serialized) // 4. If the result exceeds
    MAX_RESULT_TOKENS, rows are dropped from the end until the serialized
    size fits within TRUNCATION_TARGET_TOKENS.

    Args:
        result: The ToolResult to serialize.

    Returns:
        A tuple of (json_string, was_truncated). If truncated, the string
        includes a notice appended after the JSON.
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
    while rows and (
        len(
            json.dumps(
                {"results": rows, "total_count": result.total_count, "truncated": True},
                default=str,
            )
        )
        // 4
        > TRUNCATION_TARGET_TOKENS
    ):
        rows.pop()

    truncated_data = {
        "results": rows,
        "total_count": result.total_count,
        "truncated": True,
    }
    serialized = json.dumps(truncated_data, default=str)
    notice = (
        f"\n[Truncated: showing first {len(rows)} of {len(result.results)} rows. "
        f"{result.total_count} total matching records. "
        f"Refine your query or request specific IDs.]"
    )
    return serialized + notice, True
