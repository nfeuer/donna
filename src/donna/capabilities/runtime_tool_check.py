"""Runtime tool-availability check for capability dispatch (slice 22).

Wraps :class:`donna.skills.tool_registry.ToolRegistry.list_tool_names`
+ :class:`donna.capabilities.tool_requirements.SkillToolRequirementsLookup`
into a single call: given a capability name, return the list of
required tools that aren't currently registered.

Used by :class:`donna.automations.dispatcher.AutomationDispatcher`
right before invoking a skill — if the result is non-empty the
dispatcher surfaces a high-severity :class:`donna.cost.tool_gap.ToolGap`
per missing tool and short-circuits the run.

Realizes docs/superpowers/specs/manual-escalation.md §7 (high-blocking
detection path).
"""

from __future__ import annotations

import structlog

from donna.capabilities.tool_requirements import SkillToolRequirementsLookup
from donna.skills.tool_registry import ToolRegistry

logger = structlog.get_logger()


class RuntimeToolCheck:
    """Compares declared tool requirements against the live registry."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        lookup: SkillToolRequirementsLookup,
    ) -> None:
        self._registry = registry
        self._lookup = lookup

    async def check(self, capability_name: str) -> list[str]:
        """Return missing tool names (empty list if all present).

        ``list_required_tools`` returns ``[]`` for unknown / unmapped
        capabilities, so this method is a non-issue for non-skill paths.
        """
        try:
            required = await self._lookup.list_required_tools(capability_name)
        except Exception:
            logger.exception(
                "runtime_tool_check_lookup_failed",
                capability_name=capability_name,
            )
            return []
        if not required:
            return []
        registered = set(self._registry.list_tool_names())
        missing = [t for t in required if t not in registered]
        if missing:
            logger.info(
                "runtime_tool_check_missing",
                capability_name=capability_name,
                missing=missing,
            )
        return missing
