"""Persona-voice capture confirmations (see prompts/donna_persona.md).

Templates, not LLM output: deterministic, zero-token, and consistent with
Donna's voice — confident, specific times, clear options when she needs you.
"""

from __future__ import annotations

from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent


def _fmt_range(slot: ScheduledSlot) -> str:
    # e.g. "Friday, Jun 6, 2:00–2:30 PM"
    day = slot.start.strftime("%A, %b ") + str(slot.start.day)
    start = slot.start.strftime("%-I:%M").lstrip("0")
    end = slot.end.strftime("%-I:%M %p").lstrip("0")
    return f"{day}, {start}–{end}"


def capture_confirmation(
    *,
    title: str,
    domain: str,
    priority: int,
    time_intent: TimeIntent,
    slot: ScheduledSlot | None,
    no_slot: bool = False,
) -> str:
    """Return the message Donna sends after capturing a task.

    Args:
        title: Task title as captured.
        domain: Task domain (personal, work, family).
        priority: Task priority (1–5).
        time_intent: Structured temporal intent for the task.
        slot: Confirmed scheduled slot, or None if not yet scheduled.
        no_slot: True when a time-bound task couldn't find an open slot.

    Returns:
        A persona-voice confirmation string ready to send to the user.
    """
    tag = f"({domain} · P{priority})"
    is_time_bound = time_intent.kind in ("exact", "window", "constrained")

    if time_intent.kind == "recurring":
        human = (time_intent.recurrence or {}).get("human_readable", "on your schedule")
        return f"**{human}.** Done. — {title}"

    # A time-bound task with no slot couldn't be placed — never report it as
    # "no deadline". This covers both the explicit no_slot flag and the
    # defensive case where a dated task arrives without a slot.
    if no_slot or (slot is None and is_time_bound):
        return (
            f"'{title}' is tight — I couldn't find a slot before your deadline. "
            f"Want me to move something to make room, or take the next opening?"
        )

    if slot is not None:
        if time_intent.kind in ("window", "constrained"):
            return (
                f"Penciled '{title}' in for **{_fmt_range(slot)}** — it's flexible, "
                f"I'll tighten it as your week fills. {tag}"
            )
        return f"Done. {title} — **{_fmt_range(slot)}**. {tag}"

    # No time expressed and nothing scheduled.
    return (
        f"Filed '{title}' in your backlog. No deadline, so I'll raise it in your "
        f"weekly plan — unless you tell me it matters sooner."
    )
