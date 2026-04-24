"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup via
register_default_tools().
"""
from __future__ import annotations

from functools import partial
from typing import Any

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools.calendar_read import calendar_read
from donna.skills.tools.cost_summary import cost_summary
from donna.skills.tools.email_read import email_read
from donna.skills.tools.gmail_get_message import gmail_get_message
from donna.skills.tools.gmail_search import gmail_search
from donna.skills.tools.html_extract import html_extract
from donna.skills.tools.rss_fetch import rss_fetch
from donna.skills.tools.task_db_read import task_db_read
from donna.skills.tools.vault_link import vault_link
from donna.skills.tools.vault_list import vault_list
from donna.skills.tools.vault_read import vault_read
from donna.skills.tools.vault_undo_last import vault_undo_last
from donna.skills.tools.vault_write import vault_write
from donna.skills.tools.web_fetch import web_fetch

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
    calendar_client: Any | None = None,
    task_db: Any | None = None,
    cost_tracker: Any | None = None,
    vault_client: Any | None = None,
    vault_writer: Any | None = None,
) -> None:
    """Register built-in skill tools.

    Always registers: web_fetch, rss_fetch, html_extract.
    Each client-bound tool registers only when its dependency is provided;
    tests / degraded-mode boot that omit a client still work correctly but
    the dependent tool simply isn't available.
    """
    registry.register("web_fetch", web_fetch)
    registry.register("rss_fetch", rss_fetch)
    registry.register("html_extract", html_extract)

    if gmail_client is not None:
        registry.register(
            "gmail_search",
            partial(gmail_search, client=gmail_client),
        )
        registry.register(
            "gmail_get_message",
            partial(gmail_get_message, client=gmail_client),
        )
        registry.register(
            "email_read",
            partial(email_read, client=gmail_client),
        )

    if calendar_client is not None:
        registry.register(
            "calendar_read",
            partial(calendar_read, client=calendar_client),
        )

    if task_db is not None:
        registry.register(
            "task_db_read",
            partial(task_db_read, client=task_db),
        )

    if cost_tracker is not None:
        registry.register(
            "cost_summary",
            partial(cost_summary, client=cost_tracker),
        )

    if vault_client is not None:
        registry.register(
            "vault_read",
            partial(vault_read, client=vault_client),
        )
        registry.register(
            "vault_list",
            partial(vault_list, client=vault_client),
        )
        registry.register(
            "vault_link",
            partial(vault_link, client=vault_client),
        )

    if vault_writer is not None:
        registry.register(
            "vault_write",
            partial(vault_write, client=vault_writer),
        )
        registry.register(
            "vault_undo_last",
            partial(vault_undo_last, client=vault_writer),
        )


__all__ = [
    "DEFAULT_TOOL_REGISTRY",
    "calendar_read",
    "cost_summary",
    "email_read",
    "gmail_get_message",
    "gmail_search",
    "html_extract",
    "register_default_tools",
    "rss_fetch",
    "task_db_read",
    "vault_link",
    "vault_list",
    "vault_read",
    "vault_undo_last",
    "vault_write",
    "web_fetch",
]
