"""DiscordIntentDispatcher — post-challenger routing to task/automation/escalation."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from donna.orchestrator.discord_intent_dispatcher import (
    DiscordIntentDispatcher,
    DispatchResult,
)
from donna.agents.challenger_agent import ChallengerMatchResult
from donna.agents.claude_novelty_judge import NoveltyVerdict


class _FakeChallenger:
    def __init__(self, result: ChallengerMatchResult) -> None:
        self._result = result
    async def match_and_extract(self, msg, user_id):
        return self._result


class _FakeNovelty:
    def __init__(self, verdict: NoveltyVerdict | None) -> None:
        self._verdict = verdict
    async def evaluate(self, msg, user_id):
        return self._verdict


class _FakeAutomationRepo:
    def __init__(self):
        self.created = []
    async def create(self, **kwargs):
        self.created.append(kwargs)
        return "auto-1"


class _FakeTasksDb:
    def __init__(self):
        self.tasks = []
    async def insert_task(self, **kwargs):
        self.tasks.append(kwargs)
        return "task-1"


class _FakePendingDrafts:
    def __init__(self):
        self.drafts = []
    def set(self, d): self.drafts.append(d)
    def get_by_thread(self, tid): return None
    def discard(self, tid): pass


class _FakeNotifier:
    async def post_to_channel(self, channel_id, content): pass


@dataclass
class _Msg:
    content: str
    author_id: str = "u1"
    thread_id: int | None = None


@pytest.mark.asyncio
async def test_ready_task_routes_to_task_path() -> None:
    result = ChallengerMatchResult(status="ready", intent_kind="task", confidence=0.9)
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="get oil change by wednesday"))
    assert isinstance(out, DispatchResult)
    assert out.kind == "task_created"


@pytest.mark.asyncio
async def test_ready_automation_returns_confirmation_needed() -> None:
    result = ChallengerMatchResult(
        status="ready", intent_kind="automation", confidence=0.9,
        schedule={"cron": "0 12 * * *", "human_readable": "daily at noon"},
        extracted_inputs={"url": "x"},
    )
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="watch this daily"))
    assert out.kind == "automation_confirmation_needed"
    assert out.draft_automation is not None


@pytest.mark.asyncio
async def test_needs_input_sets_pending_draft() -> None:
    result = ChallengerMatchResult(
        status="needs_input", intent_kind="automation",
        clarifying_question="Which URL?",
        missing_fields=["url"], confidence=0.75,
    )
    drafts = _FakePendingDrafts()
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=drafts,
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="watch the jacket", thread_id=99))
    assert out.kind == "clarification_posted"
    assert len(drafts.drafts) == 1


@pytest.mark.asyncio
async def test_escalate_calls_novelty_judge_and_routes_automation() -> None:
    challenger_result = ChallengerMatchResult(status="escalate_to_claude")
    verdict = NoveltyVerdict(
        intent_kind="automation", trigger_type="on_schedule",
        extracted_inputs={"from": "jane@x.com"},
        schedule={"cron": "0 */1 * * *", "human_readable": "hourly"},
        deadline=None, alert_conditions=None,
        polling_interval_suggestion="0 */1 * * *",
        skill_candidate=True, skill_candidate_reasoning="email triage",
        clarifying_question=None,
    )
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(challenger_result),
        novelty_judge=_FakeNovelty(verdict),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="when I get an email from jane@x.com, message me"))
    assert out.kind == "automation_confirmation_needed"
