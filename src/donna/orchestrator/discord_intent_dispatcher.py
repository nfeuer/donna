"""DiscordIntentDispatcher — routes free-text messages to task/automation/escalate.

Called once per inbound Discord message by DonnaBot.on_message.
Returns a DispatchResult indicating what action the caller should take.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from donna.agents.challenger_agent import ChallengerAgent, ChallengerMatchResult
from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge, NoveltyVerdict
from donna.automations.cadence_policy import CadencePolicy, PausedState
from donna.automations.cadence_reclamper import (
    _cron_min_interval_seconds,
    _seconds_to_cron,
)
from donna.integrations.discord_pending_drafts import (
    PendingDraft,
    PendingDraftRegistry,
)

logger = structlog.get_logger()


@dataclass
class DraftAutomation:
    user_id: str
    capability_name: str | None
    inputs: dict[str, Any]
    schedule_cron: str | None
    schedule_human: str | None
    alert_conditions: dict[str, Any] | None
    target_cadence_cron: str
    active_cadence_cron: str | None
    skill_candidate: bool = False
    skill_candidate_reasoning: str | None = None


@dataclass
class DispatchResult:
    kind: str  # task_created | automation_confirmation_needed | clarification_posted | chat | no_action
    task_id: str | None = None
    draft_automation: DraftAutomation | None = None
    clarifying_question: str | None = None


class _HasContent(Protocol):
    content: str
    author_id: str
    thread_id: int | None


class DiscordIntentDispatcher:
    def __init__(
        self,
        *,
        challenger: ChallengerAgent,
        novelty_judge: ClaudeNoveltyJudge,
        pending_drafts: PendingDraftRegistry,
        tasks_db: Any,
        cadence_policy: CadencePolicy | None = None,
        lifecycle_lookup: Any | None = None,
    ) -> None:
        self._challenger = challenger
        self._novelty = novelty_judge
        self._drafts = pending_drafts
        self._tasks = tasks_db
        self._policy = cadence_policy
        self._lifecycle = lifecycle_lookup

    async def _resolve_active_cadence(
        self, target_cron: str, capability_name: str | None
    ) -> str | None:
        """Return the active cadence cron after applying CadencePolicy clamp.

        Returns None if the current lifecycle state is paused.
        Falls back to target_cron if policy/lifecycle aren't provided.
        """
        if self._policy is None or self._lifecycle is None:
            return target_cron
        state = (
            "claude_native"
            if capability_name is None
            else await self._lifecycle.current_state(capability_name)
        )
        try:
            min_interval = self._policy.min_interval_for(state)
        except PausedState:
            return None
        target_interval = _cron_min_interval_seconds(target_cron)
        if target_interval >= min_interval:
            return target_cron
        return _seconds_to_cron(min_interval)

    def _fallback_key(self, msg: _HasContent) -> int | str:
        return msg.thread_id if msg.thread_id is not None else f"dm:{msg.author_id}"

    async def dispatch(self, msg: _HasContent) -> DispatchResult:
        # Thread-resume path
        key = self._fallback_key(msg)
        existing = self._drafts.get_by_thread(key)
        if existing is not None:
            return await self._resume(msg, existing)

        result = await self._challenger.match_and_extract(msg.content, msg.author_id)
        logger.info(
            "intent_dispatch",
            status=result.status,
            intent_kind=result.intent_kind,
            capability=(result.capability.name if result.capability else None),
            confidence=result.confidence,
        )

        if result.status in ("needs_input", "ambiguous"):
            return self._handle_needs_input(result, msg)
        if result.status == "escalate_to_claude":
            return await self._handle_escalate(msg)

        if result.status == "ready":
            if result.intent_kind == "task":
                return await self._create_task(result, msg)
            if result.intent_kind == "automation":
                return await self._build_automation_draft(result, msg)
            if result.intent_kind in ("chat", "question"):
                return DispatchResult(kind="chat")

        logger.warning("intent_dispatch_unknown_status", status=result.status)
        return DispatchResult(kind="no_action")

    def _handle_needs_input(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        draft = PendingDraft(
            user_id=msg.author_id,
            thread_id=self._fallback_key(msg),
            draft_kind=result.intent_kind,
            partial={
                "extracted_inputs": result.extracted_inputs,
                "capability_name": result.capability.name if result.capability else None,
                "missing_fields": result.missing_fields,
            },
            capability_name=result.capability.name if result.capability else None,
        )
        self._drafts.set(draft)
        return DispatchResult(
            kind="clarification_posted",
            clarifying_question=result.clarifying_question,
        )

    async def _handle_escalate(self, msg: _HasContent) -> DispatchResult:
        verdict = await self._novelty.evaluate(msg.content, msg.author_id)
        if verdict.clarifying_question:
            draft = PendingDraft(
                user_id=msg.author_id,
                thread_id=self._fallback_key(msg),
                draft_kind=verdict.intent_kind,
                partial={
                    "extracted_inputs": verdict.extracted_inputs,
                    "capability_name": None,
                    "missing_fields": [],
                    "verdict_snapshot": {
                        "trigger_type": verdict.trigger_type,
                        "schedule": verdict.schedule,
                        "alert_conditions": verdict.alert_conditions,
                        "polling_interval_suggestion": verdict.polling_interval_suggestion,
                        "skill_candidate": verdict.skill_candidate,
                        "skill_candidate_reasoning": verdict.skill_candidate_reasoning,
                    },
                },
            )
            self._drafts.set(draft)
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question=verdict.clarifying_question,
            )
        if verdict.intent_kind == "task":
            return await self._create_task_from_verdict(verdict, msg)
        if verdict.intent_kind == "automation":
            return await self._build_automation_draft_from_verdict(verdict, msg)
        return DispatchResult(kind="chat")

    async def _create_task(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        tid = await self._tasks.insert_task(
            user_id=msg.author_id,
            title=msg.content,
            inputs=result.extracted_inputs,
            deadline=result.deadline,
            capability_name=(result.capability.name if result.capability else None),
        )
        return DispatchResult(kind="task_created", task_id=tid)

    async def _create_task_from_verdict(
        self, verdict: NoveltyVerdict, msg: _HasContent
    ) -> DispatchResult:
        tid = await self._tasks.insert_task(
            user_id=msg.author_id,
            title=msg.content,
            inputs=verdict.extracted_inputs,
            deadline=verdict.deadline,
            capability_name=None,
        )
        return DispatchResult(kind="task_created", task_id=tid)

    async def _build_automation_draft(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        schedule = result.schedule or {}
        target_cron = schedule.get("cron") or "0 12 * * *"
        capability_name = result.capability.name if result.capability else None
        active_cron = await self._resolve_active_cadence(target_cron, capability_name)
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=capability_name,
            inputs=result.extracted_inputs,
            schedule_cron=target_cron,
            schedule_human=schedule.get("human_readable"),
            alert_conditions=result.alert_conditions,
            target_cadence_cron=target_cron,
            active_cadence_cron=active_cron,  # None when paused
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    async def _build_automation_draft_from_verdict(
        self, verdict: NoveltyVerdict, msg: _HasContent
    ) -> DispatchResult:
        target_cron = (
            verdict.polling_interval_suggestion
            or (verdict.schedule or {}).get("cron")
            or "0 12 * * *"
        )
        active_cron = await self._resolve_active_cadence(target_cron, capability_name=None)
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=None,
            inputs=verdict.extracted_inputs,
            schedule_cron=target_cron,
            schedule_human=(verdict.schedule or {}).get("human_readable"),
            alert_conditions=verdict.alert_conditions,
            target_cadence_cron=target_cron,
            active_cadence_cron=active_cron,  # None when paused
            skill_candidate=verdict.skill_candidate,
            skill_candidate_reasoning=verdict.skill_candidate_reasoning,
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    async def _resume(
        self, msg: _HasContent, existing: PendingDraft
    ) -> DispatchResult:
        # Merge the user's reply into the partial context and re-parse.
        existing_inputs = (
            existing.partial.get("extracted_inputs") or {}
            if isinstance(existing.partial, dict)
            else {}
        )
        merged_message = (
            f"Previous context (capability={existing.capability_name}): "
            f"{json.dumps(existing_inputs)}\n"
            f"User reply: {msg.content}"
        )
        result = await self._challenger.match_and_extract(merged_message, msg.author_id)
        self._drafts.discard(existing.thread_id)
        if result.status == "ready":
            if result.intent_kind == "task":
                return await self._create_task(result, msg)
            if result.intent_kind == "automation":
                return await self._build_automation_draft(result, msg)
        if result.status in ("needs_input", "ambiguous"):
            self._drafts.set(PendingDraft(
                user_id=msg.author_id,
                thread_id=self._fallback_key(msg),
                draft_kind=result.intent_kind,
                partial={"extracted_inputs": result.extracted_inputs,
                         "capability_name": result.capability.name if result.capability else None,
                         "missing_fields": result.missing_fields},
            ))
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question=result.clarifying_question,
            )
        return DispatchResult(kind="no_action")
