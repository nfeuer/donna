"""DiscordIntentDispatcher — routes free-text messages to task/automation/escalate.

Called once per inbound Discord message by DonnaBot.on_message.
Returns a DispatchResult indicating what action the caller should take.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from donna.agents.challenger_agent import ChallengerAgent, ChallengerMatchResult
from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge, NoveltyVerdict
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
    active_cadence_cron: str
    skill_candidate: bool = True
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
        automation_repo: Any,
        tasks_db: Any,
        notifier: Any,
    ) -> None:
        self._challenger = challenger
        self._novelty = novelty_judge
        self._drafts = pending_drafts
        self._repo = automation_repo
        self._tasks = tasks_db
        self._notifier = notifier

    async def dispatch(self, msg: _HasContent) -> DispatchResult:
        # Thread-resume path
        if msg.thread_id is not None:
            existing = self._drafts.get_by_thread(msg.thread_id)
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

        if result.intent_kind == "task":
            return await self._create_task(result, msg)
        if result.intent_kind == "automation":
            return self._build_automation_draft(result, msg)
        return DispatchResult(kind="chat")

    def _handle_needs_input(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        draft = PendingDraft(
            user_id=msg.author_id,
            thread_id=msg.thread_id or 0,
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
                thread_id=msg.thread_id or 0,
                draft_kind=verdict.intent_kind,
                partial={"verdict": verdict},
            )
            self._drafts.set(draft)
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question=verdict.clarifying_question,
            )
        if verdict.intent_kind == "task":
            return await self._create_task_from_verdict(verdict, msg)
        if verdict.intent_kind == "automation":
            return self._build_automation_draft_from_verdict(verdict, msg)
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

    def _build_automation_draft(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        schedule = result.schedule or {}
        cron = schedule.get("cron") or "0 12 * * *"
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=result.capability.name if result.capability else None,
            inputs=result.extracted_inputs,
            schedule_cron=cron,
            schedule_human=schedule.get("human_readable"),
            alert_conditions=result.alert_conditions,
            target_cadence_cron=cron,
            active_cadence_cron=cron,  # Task 11 applies cadence policy clamp
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    def _build_automation_draft_from_verdict(
        self, verdict: NoveltyVerdict, msg: _HasContent
    ) -> DispatchResult:
        cron = verdict.polling_interval_suggestion or (verdict.schedule or {}).get("cron") or "0 12 * * *"
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=None,
            inputs=verdict.extracted_inputs,
            schedule_cron=cron,
            schedule_human=(verdict.schedule or {}).get("human_readable"),
            alert_conditions=verdict.alert_conditions,
            target_cadence_cron=cron,
            active_cadence_cron=cron,  # Task 11 applies cadence policy clamp
            skill_candidate=verdict.skill_candidate,
            skill_candidate_reasoning=verdict.skill_candidate_reasoning,
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    async def _resume(
        self, msg: _HasContent, existing: PendingDraft
    ) -> DispatchResult:
        # Merge the user's reply into the partial context and re-parse.
        existing_inputs = existing.partial.get("extracted_inputs", {}) if isinstance(existing.partial, dict) else {}
        merged_message = f"{existing_inputs}\n{msg.content}"
        result = await self._challenger.match_and_extract(merged_message, msg.author_id)
        self._drafts.discard(existing.thread_id)
        if result.status == "ready":
            if result.intent_kind == "task":
                return await self._create_task(result, msg)
            if result.intent_kind == "automation":
                return self._build_automation_draft(result, msg)
        if result.status in ("needs_input", "ambiguous"):
            self._drafts.set(PendingDraft(
                user_id=msg.author_id,
                thread_id=msg.thread_id or 0,
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
