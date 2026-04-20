"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup via
register_default_tools().
"""
from __future__ import annotations

from functools import partial
from typing import Any

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools.web_fetch import web_fetch
from donna.skills.tools.rss_fetch import rss_fetch
from donna.skills.tools.gmail_search import gmail_search
from donna.skills.tools.gmail_get_message import gmail_get_message


# Module-level registry populated at orchestrator startup via
# register_default_tools(DEFAULT_TOOL_REGISTRY). SkillExecutor instances
# that don't receive an explicit tool_registry default to this one.
#
# Thread-safety: None by design. Registration must complete at boot
# before any dispatch happens. Mutation after boot is not supported in
# production. Tests may call .clear() for isolation (see the autouse
# fixture in tests/conftest.py).
DEFAULT_TOOL_REGISTRY: ToolRegistry = ToolRegistry()


def register_default_tools(
    registry: ToolRegistry,
    *,
    gmail_client: Any | None = None,
) -> None:
    """Register built-in skill tools.

    Always registers: web_fetch, rss_fetch.
    Registers gmail_search + gmail_get_message only when a GmailClient is
    provided (production wiring threads the existing integration handle;
    tests / degraded-mode boot pass None).
    """
    registry.register("web_fetch", web_fetch)
    registry.register("rss_fetch", rss_fetch)

    if gmail_client is not None:
        registry.register(
            "gmail_search",
            partial(gmail_search, client=gmail_client),
        )
        registry.register(
            "gmail_get_message",
            partial(gmail_get_message, client=gmail_client),
        )


__all__ = [
    "DEFAULT_TOOL_REGISTRY",
    "register_default_tools",
    "web_fetch",
    "rss_fetch",
    "gmail_search",
    "gmail_get_message",
]
