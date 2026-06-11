"""Personal-context provider for task parsing.

Assembles a compact text block from the user's active learned-preference
rules and top-k vault notes, injected into the parse prompt so the model can
disambiguate domain and calibrate effort. Degrades to an empty string when no
sources are available or any source errors. See docs/task-system.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from donna.memory.store import MemoryStore
    from donna.preferences.rule_applier import PreferenceApplier

logger = structlog.get_logger()

_MAX_NOTES = 3
_MAX_RULES = 5


async def build_personal_context(
    raw_text: str,
    user_id: str,
    *,
    preference_applier: PreferenceApplier | None,
    memory_store: MemoryStore | None,
) -> str:
    """Return a compact context block, or "" when nothing is available.

    Never raises — a failure in any source degrades to less context, not an
    error, because parsing must not be blocked by retrieval problems.
    """
    sections: list[str] = []

    notes = await _vault_notes(raw_text, user_id, memory_store)
    if notes:
        sections.append("Known people & projects:\n" + notes)

    rules = await _preference_hints(user_id, preference_applier)
    if rules:
        sections.append("Learned preferences:\n" + rules)

    return "\n\n".join(sections)


async def _vault_notes(
    raw_text: str, user_id: str, memory_store: MemoryStore | None
) -> str:
    if memory_store is None:
        return ""
    try:
        hits = await memory_store.search(
            query=raw_text, user_id=user_id, k=_MAX_NOTES, sources=["vault"],
        )
    except Exception as exc:  # retrieval must never block parsing
        logger.warning("task_context_vault_failed", reason=str(exc), user_id=user_id)
        return ""

    lines: list[str] = []
    for hit in hits[:_MAX_NOTES]:
        title = getattr(hit, "title", None) or "(untitled)"
        snippet = " ".join(getattr(hit, "content", "").split())[:160]
        lines.append(f"- {title}: {snippet}")
    return "\n".join(lines)


async def _preference_hints(
    user_id: str, preference_applier: PreferenceApplier | None
) -> str:
    if preference_applier is None:
        return ""
    try:
        rules: list[dict[str, Any]] = await preference_applier.load_rules(user_id)
    except Exception as exc:
        logger.warning("task_context_prefs_failed", reason=str(exc), user_id=user_id)
        return ""

    lines: list[str] = []
    for rule in rules[:_MAX_RULES]:
        condition = rule.get("condition", {})
        action = rule.get("action", {})
        keywords = ", ".join(condition.get("keywords", [])) or condition.get("domain", "any")
        field = action.get("field")
        value = action.get("value")
        if field and value is not None:
            lines.append(f"- when [{keywords}] → {field} = {value}")
    return "\n".join(lines)
