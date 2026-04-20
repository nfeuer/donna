"""SkillToolRequirementsLookup — resolve which tools a capability's skill needs.

Used by AutomationCreationPath's capability-availability guard (Wave 4).
Looks up the latest skill_version for a capability and unions all tool
names from the step allowlists in the yaml_backbone.

Returns an empty list when:
  - the capability has no skill row (claude_native / not-yet-drafted skills)
  - the skill has no current_version_id
  - yaml_backbone has no steps or no tools fields
"""
from __future__ import annotations

from typing import Any

import yaml


class SkillToolRequirementsLookup:
    """Async callable: capability_name -> list[str] of required tool names."""

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    async def list_required_tools(self, capability_name: str) -> list[str]:
        """Return sorted union of tools referenced by the skill's step allowlists.

        Returns an empty list if the capability has no skill or the skill has
        no current version. Never raises.
        """
        try:
            cursor = await self._conn.execute(
                "SELECT sv.yaml_backbone FROM skill_version sv "
                "JOIN skill s ON s.current_version_id = sv.id "
                "WHERE s.capability_name = ?",
                (capability_name,),
            )
            row = await cursor.fetchone()
        except Exception:  # noqa: BLE001 — defensive; unknown capability is fine
            return []

        if row is None:
            return []

        spec = yaml.safe_load(row[0]) or {}
        tools: set[str] = set()
        for step in spec.get("steps", []):
            for tool in step.get("tools", []) or []:
                tools.add(tool)
        return sorted(tools)
