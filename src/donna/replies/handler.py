"""Universal Reply Handler — confidence-gated pipeline for user replies.

Layer 1 (FastPath): Config-driven keyword matching with a complexity
gate that prevents misclassification of multi-intent replies.

Layer 2 (LLM): Local LLM classifies complex replies, proposes actions,
and drafts a response in Donna's persona.

Plan-and-confirm: LLM-proposed actions are persisted and require
user confirmation before execution.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import structlog

from donna.config import ReplyIntentsConfig

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class FastPathResult:
    """Result from fast-path keyword matching."""

    intent: str
    action: str
    confirm: bool


class FastPath:
    """Layer 1: keyword matching with complexity gate.

    Args:
        config: Parsed ReplyIntentsConfig.
    """

    def __init__(self, config: ReplyIntentsConfig) -> None:
        self._config = config
        self._fp = config.fast_path

    def is_simple(self, reply: str) -> bool:
        """Check whether a reply passes the complexity gate."""
        if len(reply) > self._fp.max_length:
            return False

        lower = reply.lower()
        for signal in self._fp.multi_intent_signals:
            if signal in lower:
                return False

        if "," in reply and len(reply.split(",")) > 2:
            return False

        matched_intents: list[str] = []
        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                matched_intents.append(name)

        return len(matched_intents) == 1

    def match(self, reply: str) -> FastPathResult | None:
        """Try to match a reply to a single intent. Returns None if no match or complex."""
        lower = reply.lower()
        if not self.is_simple(reply):
            return None

        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                return FastPathResult(
                    intent=name,
                    action=intent.action,
                    confirm=intent.confirm,
                )
        return None

    def is_plan_confirm(self, reply: str) -> bool:
        """Check if reply is confirming a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.confirm_keywords)

    def is_plan_reject(self, reply: str) -> bool:
        """Check if reply is rejecting a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.reject_keywords)


@dataclasses.dataclass
class ReplyResult:
    """Result of processing a user reply."""

    path: str  # "fast", "llm", "plan_confirmed", "plan_rejected", "plan_cancelled"
    action: str | None = None
    actions: list[dict[str, Any]] | None = None
    reply_to_user: str | None = None
    pending_plan_id: str | None = None
    execution_results: list[str] | None = None


class ReplyHandler:
    """Universal Reply Handler — confidence-gated pipeline.

    Args:
        conn: aiosqlite connection (for memory and plans tables).
        intents_config: FastPath keyword config.
        actions_config: Action registry config.
        router: ModelRouter for LLM calls.
        db: Database for executing task actions.
        context: Shared context dict (scheduler, calendar_client, etc.).
    """

    def __init__(
        self,
        conn: Any,
        intents_config: ReplyIntentsConfig,
        actions_config: Any,
        router: Any,
        db: Any,
        context: dict[str, Any],
    ) -> None:
        from donna.replies.action_registry import ActionRegistry
        from donna.replies.llm_classifier import LLMClassifier
        from donna.replies.memory import ThreadMemory
        from donna.replies.pending_plans import PendingPlans

        self._db = db
        self._context = context
        self._fast_path = FastPath(intents_config)
        self._registry = ActionRegistry(actions_config)
        self._memory = ThreadMemory(conn, window_size=actions_config.memory.window_size)
        self._plans = PendingPlans(conn, expiry_minutes=actions_config.plan.expiry_minutes)
        self._classifier = LLMClassifier(
            router=router, registry=self._registry, memory=self._memory,
        )

    async def handle(
        self,
        thread_id: str,
        reply: str,
        task: Any,
        context_type: str,
    ) -> ReplyResult:
        """Process a user reply through the confidence-gated pipeline."""
        await self._memory.record(thread_id, context_type, getattr(task, "id", None), "user", reply)

        # --- Pending plan intercept ---
        pending = await self._plans.get_pending(thread_id)
        if pending is not None:
            if self._fast_path.is_plan_confirm(reply):
                return await self._execute_plan(thread_id, pending, task)
            elif self._fast_path.is_plan_reject(reply):
                await self._plans.reject(thread_id)
                return ReplyResult(path="plan_rejected", reply_to_user="Got it, cancelled.")
            else:
                await self._plans.reject(thread_id)
                # Fall through to process the new reply

        # --- Layer 1: Fast path ---
        match = self._fast_path.match(reply)
        if match is not None:
            result = await self._execute_fast(thread_id, match, task, context_type)
            return result

        # --- Layer 2: LLM path ---
        llm_result = await self._classifier.classify(
            thread_id=thread_id,
            user_reply=reply,
            task=task,
            context_type=context_type,
        )

        actions = llm_result.get("actions", [])
        reply_to_user = llm_result.get("reply_to_user", "")

        task_id = getattr(task, "id", None)
        if not actions:
            await self._memory.record(
                thread_id, context_type, task_id, "donna", reply_to_user,
            )
            return ReplyResult(path="llm", reply_to_user=reply_to_user)

        plan_id = await self._plans.save(thread_id, actions, reply_to_user)
        await self._memory.record(
            thread_id, context_type, task_id, "donna", reply_to_user,
        )

        return ReplyResult(
            path="llm",
            actions=actions,
            reply_to_user=reply_to_user,
            pending_plan_id=plan_id,
        )

    async def _execute_fast(
        self, thread_id: str, match: FastPathResult, task: Any, context_type: str,
    ) -> ReplyResult:
        """Execute a fast-path action immediately."""
        import importlib

        action_name = match.action
        action_def = self._registry.get_action_def(action_name)
        if action_def is None:
            return ReplyResult(path="fast", action=action_name, reply_to_user="Action not found.")

        handler_path = action_def.handler
        module_path, func_name = handler_path.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        handler_fn = getattr(mod, func_name)

        context = {**self._context, "task_id": getattr(task, "id", None)}
        params = {"task_id": getattr(task, "id", None)}

        try:
            result_msg = await handler_fn(self._db, context, params)
        except Exception:
            logger.exception("fast_path_execute_failed", action=action_name)
            result_msg = f"Failed to execute {action_name}."

        await self._memory.record(
            thread_id, context_type, getattr(task, "id", None), "donna", result_msg,
        )

        return ReplyResult(
            path="fast", action=action_name,
            reply_to_user=result_msg, execution_results=[result_msg],
        )

    async def _execute_plan(
        self, thread_id: str, pending: dict[str, Any], task: Any,
    ) -> ReplyResult:
        """Execute a confirmed pending plan."""
        import importlib
        import json

        plan = await self._plans.confirm(thread_id)
        if plan is None:
            return ReplyResult(path="plan_confirmed", reply_to_user="No plan to confirm.")

        actions = json.loads(plan["actions_json"])
        results: list[str] = []

        for action in actions:
            action_name = action.get("action", "")
            action_def = self._registry.get_action_def(action_name)
            if action_def is None:
                results.append(f"Unknown action: {action_name}")
                continue

            handler_path = action_def.handler
            module_path, func_name = handler_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            handler_fn = getattr(mod, func_name)

            context = {**self._context, "task_id": getattr(task, "id", None)}
            params = action.get("params", {})
            if "task_id" not in params:
                params["task_id"] = getattr(task, "id", None)

            try:
                result_msg = await handler_fn(self._db, context, params)
                results.append(result_msg)
            except Exception:
                logger.exception("plan_execute_action_failed", action=action_name)
                results.append(f"Failed: {action_name}")

        summary = " ".join(results)
        await self._memory.record(
            thread_id, "overdue", getattr(task, "id", None), "donna", summary,
        )

        return ReplyResult(
            path="plan_confirmed",
            actions=actions,
            reply_to_user=summary,
            execution_results=results,
        )
