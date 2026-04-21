"""CapabilityToolRegistryCheck — boot-time assertion.

Reads ``capability.tools_json`` for every seeded capability and verifies
that every named tool is registered in the supplied :class:`ToolRegistry`.
Raises :class:`CapabilityToolConfigError` with the full set of mismatches
when any capability references an unregistered tool.

Runs once at startup, after ``register_default_tools`` and
``SeedCapabilityLoader.load_and_upsert`` have both completed. Fail-loud:
a mismatch means the orchestrator refuses to start rather than silently
dropping to the legacy ad_hoc path at runtime.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from donna.skills.tool_registry import ToolRegistry

logger = structlog.get_logger()


class CapabilityToolConfigError(Exception):
    """Raised when a capability declares a tool that isn't registered."""


class CapabilityToolRegistryCheck:
    """Assert every capability's declared tool exists in the registry."""

    def __init__(self, registry: ToolRegistry, connection: Any) -> None:
        self._registry = registry
        self._conn = connection

    async def validate_all(self) -> None:
        """Raise :class:`CapabilityToolConfigError` on any mismatch."""
        cursor = await self._conn.execute(
            "SELECT name, tools_json FROM capability WHERE tools_json IS NOT NULL"
        )
        rows = await cursor.fetchall()

        registered = set(self._registry.list_tool_names())
        mismatches: list[tuple[str, str]] = []
        for name, tools_blob in rows:
            try:
                declared = json.loads(tools_blob) if tools_blob else []
            except (TypeError, ValueError):
                logger.warning(
                    "capability_tools_json_invalid",
                    capability=name,
                    tools_blob=tools_blob,
                )
                mismatches.append((name, "<invalid tools_json>"))
                continue
            if not isinstance(declared, list):
                mismatches.append((name, "<tools_json not a list>"))
                continue
            for tool in declared:
                if tool not in registered:
                    mismatches.append((name, tool))

        if mismatches:
            pairs = ", ".join(f"{cap}→{tool}" for cap, tool in mismatches)
            logger.error(
                "capability_tool_registry_check_failed",
                mismatches=mismatches,
                registered=sorted(registered),
            )
            raise CapabilityToolConfigError(
                f"{len(mismatches)} capability→tool mismatch(es): {pairs}"
            )

        logger.info(
            "capability_tool_registry_check_passed",
            checked=len(rows),
            registered_tools=sorted(registered),
        )
