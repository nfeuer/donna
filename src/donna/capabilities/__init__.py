"""Capability registry — user-facing task patterns Donna can handle.

See docs/superpowers/specs/archive/2026-04-15-skill-system-and-challenger-refactor-design.md
"""

from donna.capabilities.models import CapabilityRow, row_to_capability

__all__ = ["CapabilityRow", "row_to_capability"]
