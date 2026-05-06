"""Tool-gap value objects (slice 22).

Defines :class:`ToolGap`, the immutable record passed from every
detection point (boot check, scheduler pre-run, automation creation,
skill draft pre-flight, runtime dispatch trip-wire) to
:class:`donna.cost.tool_gap_surfacer.ToolGapSurfacer`.

Realizes docs/superpowers/specs/manual-escalation.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# ----------------------------------------------------------------------
# Severity
# ----------------------------------------------------------------------

SEVERITY_HIGH = "high"
"""Real-time Discord ping. Capability is active and cannot run."""

SEVERITY_SPECULATIVE = "speculative"
"""Filed silently; surfaces in morning digest only."""

Severity = Literal["high", "speculative"]


# ----------------------------------------------------------------------
# Detection points (logged into ``tool_request.detection_point``)
# ----------------------------------------------------------------------

DETECTION_BOOT_CHECK = "capability_tool_check"
DETECTION_SCHEDULER = "scheduler_pre_run"
DETECTION_AUTOMATION_CREATE = "automation_creation"
DETECTION_SKILL_DRAFT = "skill_draft"
DETECTION_RUNTIME_DISPATCH = "runtime_dispatch"


# ----------------------------------------------------------------------
# Status / priority defaults
# ----------------------------------------------------------------------

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"

DEFAULT_PRIORITY = 3


@dataclass(frozen=True)
class ToolGap:
    """Detection-time record of a missing tool.

    All fields except ``severity``, ``user_id``, ``tool_name``,
    ``rationale`` and ``detection_point`` are best-effort.
    ``proposed_signature`` is opaque JSON shaped roughly as Python type
    hints (see canonical spec §7) — kept loose so callers can populate
    only what they know:

    .. code-block:: json

        {
          "name": "fetch_url",
          "params": [{"name": "url", "type": "str", "required": true}],
          "returns": "dict",
          "summary": "Fetch URL and return JSON-decoded body",
          "errors_raised": ["TimeoutError"]
        }

    Attributes:
        tool_name: Name of the unregistered tool.
        user_id: Owner of the gap (multi-user-ready, slice 22 has one user).
        severity: ``high`` or ``speculative``; controls surfacing.
        blocking_capability_id: Capability that needs the tool. ``None``
            for purely speculative gaps from skill drafts.
        rationale: Free-text explaining why the gap appeared.
        proposed_signature: Optional sketch of the tool's API.
        detection_point: Subsystem that surfaced the gap.
        priority: 1-5 like tasks; higher = more urgent. Defaults to 3.
    """

    tool_name: str
    user_id: str
    severity: Severity
    blocking_capability_id: str | None
    rationale: str
    proposed_signature: dict[str, Any] | None
    detection_point: str
    priority: int = DEFAULT_PRIORITY
