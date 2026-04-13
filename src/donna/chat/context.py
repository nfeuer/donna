"""Context assembly for chat prompts.

Builds the context blocks that get injected into chat prompt templates
based on intent type and session state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from donna.chat.types import ChatIntent, ChatMessage


def build_session_context(
    messages: list[ChatMessage],
    pinned_task: dict[str, Any] | None,
) -> str:
    """Build the session context block for the prompt.

    Includes conversation history and pinned task details.
    """
    parts: list[str] = []

    if pinned_task:
        parts.append("## Pinned Task")
        parts.append(f"**{pinned_task.get('title', 'Untitled')}**")
        if pinned_task.get("description"):
            parts.append(f"Description: {pinned_task['description']}")
        parts.append(f"Status: {pinned_task.get('status', 'unknown')}")
        parts.append(f"Priority: {pinned_task.get('priority', 'unknown')}")
        if pinned_task.get("notes"):
            parts.append(f"Notes: {pinned_task['notes']}")
        parts.append("")

    if messages:
        parts.append("## Conversation History")
        for msg in messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            parts.append(f"{role_label}: {msg.content}")
        parts.append("")

    return "\n".join(parts)


def build_intent_context(
    intent: ChatIntent,
    tasks: list[dict[str, Any]] | None = None,
    schedule_summary: str | None = None,
    open_task_count: int | None = None,
    agent_outputs: list[dict[str, Any]] | None = None,
) -> str:
    """Build intent-specific context for the prompt."""
    if intent == ChatIntent.FREEFORM:
        return ""

    if intent == ChatIntent.ESCALATION_REQUEST:
        return ""

    parts: list[str] = []

    if (
        intent in (ChatIntent.TASK_QUERY, ChatIntent.TASK_ACTION, ChatIntent.PLANNING)
        and tasks
    ):
        parts.append("## Active Tasks")
        for t in tasks:
            parts.append(
                f"- [{t.get('status', '?')}] {t.get('title', 'Untitled')} "
                f"(P{t.get('priority', '?')}, {t.get('domain', '?')})"
            )
        parts.append("")

    if intent == ChatIntent.PLANNING:
        if schedule_summary:
            parts.append(f"## Schedule\n{schedule_summary}\n")
        if open_task_count is not None:
            parts.append(f"Open tasks across all domains: {open_task_count}\n")

    if intent == ChatIntent.AGENT_OUTPUT_QUERY and agent_outputs:
        parts.append("## Agent Outputs")
        for ao in agent_outputs:
            parts.append(
                f"- [{ao.get('task_type', '?')}] {ao.get('model_actual', '?')}: "
                f"{str(ao.get('output', ''))[:500]}"
            )
        parts.append("")

    return "\n".join(parts)


def render_chat_prompt(
    template: str,
    user_input: str,
    user_name: str = "Nick",
    session_context: str = "",
    intent_context: str = "",
    conversation_history: str = "",
) -> str:
    """Render a chat prompt template with variables."""
    now = datetime.now(UTC)
    return (
        template
        .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
        .replace("{{ current_time }}", now.strftime("%H:%M %Z"))
        .replace("{{ user_name }}", user_name)
        .replace("{{ user_input }}", user_input)
        .replace("{{ session_context }}", session_context)
        .replace("{{ intent_context }}", intent_context)
        .replace("{{ conversation_history }}", conversation_history)
    )
