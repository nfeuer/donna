"""CapabilityToolRegistryCheck — boot-time assertion + slice-22 surfacing.

Reads ``capability.tools_json`` for every seeded capability and verifies
that every named tool is registered in the supplied :class:`ToolRegistry`.

**Behavior:**

- Mismatches on **active + scheduled/messaged** capabilities are
  fatal — the orchestrator refuses to start rather than silently
  dropping to the legacy ad_hoc path at runtime. This preserves the
  fail-loud guarantee from before slice 22.
- Mismatches on capabilities with ``status='pending_review'`` or
  ``trigger_type='on_manual'`` are filed as **speculative**
  :class:`donna.cost.tool_gap.ToolGap` rows when a
  :class:`donna.cost.tool_gap_surfacer.ToolGapSurfacer` is wired,
  then the boot still runs (provided no fatal subset remains).

This split lets the morning digest surface latent gaps without
forcing the operator to clean every dormant capability before the
orchestrator can start.

Realizes docs/superpowers/specs/manual-escalation.md §7 (boot-time
detection path).
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from donna.cost.tool_gap import (
    DETECTION_BOOT_CHECK,
    SEVERITY_SPECULATIVE,
    ToolGap,
)
from donna.skills.tool_registry import ToolRegistry

logger = structlog.get_logger()


class CapabilityToolConfigError(Exception):
    """Raised when an active+scheduled capability has a missing tool."""


class CapabilityToolRegistryCheck:
    """Assert every capability's declared tool exists in the registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        connection: Any,
        *,
        surfacer: Any | None = None,
        boot_owner_user_id: str = "boot",
    ) -> None:
        self._registry = registry
        self._conn = connection
        self._surfacer = surfacer
        self._boot_owner_user_id = boot_owner_user_id

    async def validate_all(self) -> None:
        """Surface speculative gaps; raise on fatal mismatches."""
        cursor = await self._conn.execute(
            "SELECT name, tools_json, status, trigger_type "
            "FROM capability WHERE tools_json IS NOT NULL"
        )
        rows = await cursor.fetchall()

        registered = set(self._registry.list_tool_names())
        fatal: list[tuple[str, str]] = []
        speculative: list[tuple[str, str]] = []
        for name, tools_blob, status, trigger_type in rows:
            try:
                declared = json.loads(tools_blob) if tools_blob else []
            except (TypeError, ValueError):
                logger.warning(
                    "capability_tools_json_invalid",
                    capability=name,
                    tools_blob=tools_blob,
                )
                fatal.append((name, "<invalid tools_json>"))
                continue
            if not isinstance(declared, list):
                fatal.append((name, "<tools_json not a list>"))
                continue
            is_speculative = status == "pending_review" or trigger_type == "on_manual"
            for tool in declared:
                if tool in registered:
                    continue
                if is_speculative:
                    speculative.append((name, tool))
                else:
                    fatal.append((name, tool))

        # Surface speculative gaps regardless of fatal subset — the rows
        # are still useful when the operator boots after fixing the
        # fatal ones.
        if speculative and self._surfacer is not None:
            for cap_name, tool_name in speculative:
                try:
                    await self._surfacer.surface(
                        ToolGap(
                            tool_name=tool_name,
                            user_id=self._boot_owner_user_id,
                            severity=SEVERITY_SPECULATIVE,
                            blocking_capability_id=cap_name,
                            rationale=(
                                f"capability '{cap_name}' declared tool "
                                f"'{tool_name}' which is not registered"
                            ),
                            proposed_signature=None,
                            detection_point=DETECTION_BOOT_CHECK,
                        )
                    )
                except Exception:
                    logger.exception(
                        "capability_tool_check_surface_failed",
                        capability=cap_name,
                        tool=tool_name,
                    )

        if fatal:
            pairs = ", ".join(f"{cap}→{tool}" for cap, tool in fatal)
            logger.error(
                "capability_tool_registry_check_failed",
                fatal=fatal,
                speculative=speculative,
                registered=sorted(registered),
            )
            raise CapabilityToolConfigError(
                f"{len(fatal)} active capability→tool mismatch(es): {pairs}"
            )

        logger.info(
            "capability_tool_registry_check_passed",
            checked=len(rows),
            speculative_count=len(speculative),
            registered_tools=sorted(registered),
        )
