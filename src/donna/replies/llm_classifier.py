"""LLM-based reply classifier for the Universal Reply Handler (Layer 2).

Constructs a prompt with conversation memory, task context, and
available actions, then sends to the local LLM via ModelRouter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.replies.action_registry import ActionRegistry
from donna.replies.memory import ThreadMemory

if TYPE_CHECKING:
    from donna.models.router import ModelRouter

logger = structlog.get_logger()


class LLMClassifier:
    """Classify complex replies via the local LLM.

    Args:
        router: ModelRouter instance for LLM calls.
        registry: ActionRegistry for validation and prompt rendering.
        memory: ThreadMemory for conversation context.
    """

    def __init__(
        self,
        router: ModelRouter,
        registry: ActionRegistry,
        memory: ThreadMemory,
    ) -> None:
        self._router = router
        self._registry = registry
        self._memory = memory

    async def classify(
        self,
        thread_id: str,
        user_reply: str,
        task: Any,
        context_type: str,
    ) -> dict[str, Any]:
        """Send reply + context to LLM and return validated actions.

        Args:
            thread_id: Conversation thread identifier.
            user_reply: The new message from the user.
            task: Task object with attributes (id, title, status, domain, etc.).
            context_type: The nudge context type (e.g. "overdue", "scheduled").

        Returns:
            Dict with keys: actions (list), reply_to_user (str), reasoning (str).
        """
        conversation = await self._memory.retrieve(thread_id)
        available_actions = self._registry.render_for_llm()

        prompt = (
            f"## Current Task Context\n"
            f"Task: {getattr(task, 'title', '')}\n"
            f"Status: {getattr(task, 'status', '')}\n"
            f"Domain: {getattr(task, 'domain', 'personal')}\n"
            f"Priority: {getattr(task, 'priority', 2)}\n"
            f"Scheduled start: {getattr(task, 'scheduled_start', 'unknown')}\n"
            f"Estimated duration: {getattr(task, 'estimated_duration', 0)} minutes\n\n"
            f"## Conversation History\n"
        )

        for msg in conversation:
            prompt += f"{msg['role'].upper()}: {msg['content']}\n"

        prompt += (
            f"\n## User's New Reply\n{user_reply}\n\n"
            f"## Available Actions\n{available_actions}\n"
        )

        task_id = getattr(task, "id", None)

        try:
            result, _meta = await self._router.complete(
                prompt=prompt,
                task_type="reply_intent",
                task_id=task_id,
                user_id="system",
            )
        except Exception:
            logger.exception("llm_classifier_failed", thread_id=thread_id)
            return {
                "actions": [],
                "reply_to_user": "I couldn't process that. Could you try rephrasing?",
                "reasoning": "LLM call failed",
            }

        actions = result.get("actions", [])
        reply_to_user = result.get("reply_to_user", "")
        reasoning = result.get("reasoning", "")

        valid_actions, errors = self._registry.validate_actions(actions)
        if errors:
            logger.warning("llm_actions_had_errors", errors=errors, thread_id=thread_id)

        context = {"task_id": task_id}
        valid_actions = [self._registry.inject_context(a, context) for a in valid_actions]

        logger.info(
            "llm_classify_complete",
            thread_id=thread_id,
            task_id=task_id,
            action_count=len(valid_actions),
            stripped_count=len(actions) - len(valid_actions),
        )

        return {
            "actions": valid_actions,
            "reply_to_user": reply_to_user,
            "reasoning": reasoning,
        }
