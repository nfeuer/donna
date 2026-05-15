"""Anthropic-format tool definitions for LLM tool_use on skill steps.

Maps tool names from the ToolRegistry to the JSON schema format required
by the Anthropic Messages API. Only tools listed here can be offered to
Claude via tool_use; the ToolRegistry handles server-side execution.
"""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "web_fetch": {
        "name": "web_fetch",
        "description": "Fetch a URL and return the page content (status code, headers, body).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
                "timeout_s": {
                    "type": "number",
                    "description": "Request timeout in seconds. Default 10.",
                },
            },
            "required": ["url"],
        },
    },
}


def resolve_tool_definitions(tool_names: list[str]) -> list[dict[str, Any]]:
    """Convert a list of tool names to Anthropic-format tool definitions."""
    definitions = []
    for name in tool_names:
        defn = TOOL_DEFINITIONS.get(name)
        if defn is not None:
            definitions.append(defn)
    return definitions
