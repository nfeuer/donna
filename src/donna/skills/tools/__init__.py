"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup via
register_default_tools().
"""
from __future__ import annotations

from functools import partial
from typing import Any

from donna.skills.tool_param_schemas import load_tool_param_schemas
from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools.browser_extract_text import browser_extract_text
from donna.skills.tools.browser_screenshot import browser_screenshot
from donna.skills.tools.calendar_read import calendar_read
from donna.skills.tools.cost_summary import cost_summary
from donna.skills.tools.email_read import email_read
from donna.skills.tools.gmail_get_message import gmail_get_message
from donna.skills.tools.gmail_search import gmail_search
from donna.skills.tools.html_extract import html_extract
from donna.skills.tools.memory_search import memory_search
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
    memory_store: Any | None = None,
) -> None:
    """Register built-in skill tools, each with its parameter schema.

    Always registers: web_fetch, rss_fetch, html_extract, browser_*.
    Each client-bound tool registers only when its dependency is provided;
    tests / degraded-mode boot that omit a client still work correctly but
    the dependent tool simply isn't available.

    Every tool is registered with its declarative per-tool parameter schema
    (``schemas/tools/<tool>.json``, loaded via ``load_tool_param_schemas``), so
    no production tool is ever dispatched unschema'd (R3 — §7.2 resolution;
    CLAUDE.md principle #6). Injected deps bound here via ``functools.partial``
    (``client``/``store``) are not caller-supplied and are absent from the
    schemas.

    Args:
        registry: The ToolRegistry to populate.
        gmail_client: Optional Gmail client enabling the gmail/email tools.
        calendar_client: Optional calendar client enabling ``calendar_read``.
        task_db: Optional task DB handle enabling ``task_db_read``.
        cost_tracker: Optional cost tracker enabling ``cost_summary``.
        vault_client: Optional vault reader enabling the vault read tools.
        vault_writer: Optional vault writer enabling the vault write tools.
        memory_store: Optional memory store enabling ``memory_search``.

    Returns:
        None.
    """
    schemas = load_tool_param_schemas()

    registry.register("web_fetch", web_fetch, param_schema=schemas["web_fetch"])
    registry.register("rss_fetch", rss_fetch, param_schema=schemas["rss_fetch"])
    registry.register(
        "html_extract", html_extract, param_schema=schemas["html_extract"]
    )
    registry.register(
        "browser_extract_text",
        browser_extract_text,
        param_schema=schemas["browser_extract_text"],
    )
    registry.register(
        "browser_screenshot",
        browser_screenshot,
        param_schema=schemas["browser_screenshot"],
    )

    if gmail_client is not None:
        registry.register(
            "gmail_search",
            partial(gmail_search, client=gmail_client),
            param_schema=schemas["gmail_search"],
        )
        registry.register(
            "gmail_get_message",
            partial(gmail_get_message, client=gmail_client),
            param_schema=schemas["gmail_get_message"],
        )
        registry.register(
            "email_read",
            partial(email_read, client=gmail_client),
            param_schema=schemas["email_read"],
        )

    if calendar_client is not None:
        registry.register(
            "calendar_read",
            partial(calendar_read, client=calendar_client),
            param_schema=schemas["calendar_read"],
        )

    if task_db is not None:
        registry.register(
            "task_db_read",
            partial(task_db_read, client=task_db),
            param_schema=schemas["task_db_read"],
        )

    if cost_tracker is not None:
        registry.register(
            "cost_summary",
            partial(cost_summary, client=cost_tracker),
            param_schema=schemas["cost_summary"],
        )

    if vault_client is not None:
        registry.register(
            "vault_read",
            partial(vault_read, client=vault_client),
            param_schema=schemas["vault_read"],
        )
        registry.register(
            "vault_list",
            partial(vault_list, client=vault_client),
            param_schema=schemas["vault_list"],
        )
        registry.register(
            "vault_link",
            partial(vault_link, client=vault_client),
            param_schema=schemas["vault_link"],
        )

    if vault_writer is not None:
        registry.register(
            "vault_write",
            partial(vault_write, client=vault_writer),
            param_schema=schemas["vault_write"],
        )
        registry.register(
            "vault_undo_last",
            partial(vault_undo_last, client=vault_writer),
            param_schema=schemas["vault_undo_last"],
        )

    if memory_store is not None:
        registry.register(
            "memory_search",
            partial(memory_search, store=memory_store),
            param_schema=schemas["memory_search"],
        )


__all__ = [
    "DEFAULT_TOOL_REGISTRY",
    "browser_extract_text",
    "browser_screenshot",
    "calendar_read",
    "cost_summary",
    "email_read",
    "gmail_get_message",
    "gmail_search",
    "html_extract",
    "memory_search",
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
