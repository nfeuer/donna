"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup via
`register_default_tools()`.
"""

from __future__ import annotations

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools.web_fetch import web_fetch


# Module-level registry populated at orchestrator startup via
# register_default_tools(DEFAULT_TOOL_REGISTRY). SkillExecutor instances
# that don't receive an explicit tool_registry default to this one, so
# production skill dispatch sees the same tools the orchestrator registered
# at boot. Tests that need an isolated registry should pass one explicitly.
DEFAULT_TOOL_REGISTRY: ToolRegistry = ToolRegistry()


def register_default_tools(registry: ToolRegistry) -> None:
    """Register all built-in skill tools into the given ToolRegistry.

    Called once at application startup. Add new built-in tools here as
    they come online. Skills that need a tool must declare it in their
    step's `tools:` allowlist — registration alone is not enough.
    """
    registry.register("web_fetch", web_fetch)


__all__ = ["DEFAULT_TOOL_REGISTRY", "register_default_tools", "web_fetch"]
