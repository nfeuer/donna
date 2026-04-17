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
from donna.integrations.discord_pending_drafts import (
    PendingDraft,
    PendingDraftRegistry,
)


class _FakeChallenger:
    def __init__(self, result: ChallengerMatchResult | list[ChallengerMatchResult]) -> None:
        if isinstance(result, list):
            self._results = list(result)
        else:
            self._results = [result]
        self.calls: list[tuple[str, str]] = []

    async def match_and_extract(self, msg, user_id):
        self.calls.append((msg, user_id))
        if len(self._results) == 1:
            return self._results[0]
        return self._results.pop(0)


class _FakeNovelty:
    def __init__(self, verdict: NoveltyVerdict | None) -> None:
        self._verdict = verdict
    async def evaluate(self, msg, user_id):
        return self._verdict


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
        tasks_db=_FakeTasksDb(),
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
        tasks_db=_FakeTasksDb(),
    )
    out = await dispatcher.dispatch(_Msg(content="watch this daily"))
    assert out.kind == "automation_confirmation_needed"
    assert out.draft_automation is not None
    # Capability-matched automations should NOT be skill candidates by default.
    assert out.draft_automation.skill_candidate is False


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
        tasks_db=_FakeTasksDb(),
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
        tasks_db=_FakeTasksDb(),
    )
    out = await dispatcher.dispatch(_Msg(content="when I get an email from jane@x.com, message me"))
    assert out.kind == "automation_confirmation_needed"
    # Novelty-judge-emitted automations can be skill candidates.
    assert out.draft_automation is not None
    assert out.draft_automation.skill_candidate is True


@pytest.mark.asyncio
async def test_resume_path_passes_structured_context_and_recreates_task() -> None:
    """When a prior clarification is pending, the resume path should re-parse
    the user's reply with the prior capability and extracted inputs merged in,
    and on status=ready produce the final task.
    """
    # First call (not used here — resume path uses its own re-parse).
    followup_ready = ChallengerMatchResult(
        status="ready", intent_kind="task",
        extracted_inputs={"deadline": "wednesday", "vendor": "acme"},
        confidence=0.95,
    )
    challenger = _FakeChallenger(followup_ready)

    registry = PendingDraftRegistry(ttl_seconds=1800)
    # Prime the registry with a prior partial draft keyed to thread 77.
    registry.set(PendingDraft(
        user_id="u1",
        thread_id=77,
        draft_kind="task",
        partial={"extracted_inputs": {"vendor": "acme"}, "missing_fields": ["deadline"]},
        capability_name="schedule_appointment",
    ))

    tasks = _FakeTasksDb()
    dispatcher = DiscordIntentDispatcher(
        challenger=challenger,
        novelty_judge=_FakeNovelty(None),
        pending_drafts=registry,
        tasks_db=tasks,
    )

    out = await dispatcher.dispatch(_Msg(content="wednesday", thread_id=77))

    assert out.kind == "task_created"
    # Exactly one re-parse was made and its input contained the structured context.
    assert len(challenger.calls) == 1
    merged, _ = challenger.calls[0]
    assert "capability=schedule_appointment" in merged
    assert "acme" in merged
    assert "User reply: wednesday" in merged
    # Draft was consumed on success.
    assert registry.get_by_thread(77) is None


@pytest.mark.asyncio
async def test_escalate_with_clarifying_question_stores_flattened_partial() -> None:
    """Escalate path: when novelty judge asks a clarifying question, the
    stored draft must be in the common shape expected by _resume.
    """
    challenger_result = ChallengerMatchResult(status="escalate_to_claude")
    verdict = NoveltyVerdict(
        intent_kind="automation",
        trigger_type="on_schedule",
        extracted_inputs={"from": "partial@x.com"},
        schedule={"cron": "0 */1 * * *", "human_readable": "hourly"},
        deadline=None,
        alert_conditions=None,
        polling_interval_suggestion="0 */1 * * *",
        skill_candidate=True,
        skill_candidate_reasoning="novel watcher",
        clarifying_question="What should I do when a match is found?",
    )
    registry = PendingDraftRegistry(ttl_seconds=1800)
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(challenger_result),
        novelty_judge=_FakeNovelty(verdict),
        pending_drafts=registry,
        tasks_db=_FakeTasksDb(),
    )

    out = await dispatcher.dispatch(_Msg(content="watch for emails from partial@x.com"))
    assert out.kind == "clarification_posted"
    assert out.clarifying_question == "What should I do when a match is found?"

    # thread_id is None -> falls back to dm:{user_id}
    stored = registry.get_by_thread("dm:u1")
    assert stored is not None
    # _resume expects these keys in partial:
    assert stored.partial["extracted_inputs"] == {"from": "partial@x.com"}
    assert stored.partial["capability_name"] is None
    assert stored.partial["missing_fields"] == []
    # Verdict-specific fields are preserved under verdict_snapshot.
    snap = stored.partial["verdict_snapshot"]
    assert snap["trigger_type"] == "on_schedule"
    assert snap["polling_interval_suggestion"] == "0 */1 * * *"
    assert snap["skill_candidate"] is True


@pytest.mark.asyncio
async def test_unknown_status_returns_no_action() -> None:
    """Any unexpected status should NOT silently create a task/automation."""
    weird = ChallengerMatchResult(status="something_unexpected", intent_kind="task")
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(weird),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=_FakePendingDrafts(),
        tasks_db=_FakeTasksDb(),
    )
    out = await dispatcher.dispatch(_Msg(content="hmm"))
    assert out.kind == "no_action"


@pytest.mark.asyncio
async def test_dm_fallback_key_is_per_user() -> None:
    """Without a thread_id, the fallback key must include the user id so two
    users' DMs don't collide in the registry.
    """
    needs_input = ChallengerMatchResult(
        status="needs_input", intent_kind="task",
        clarifying_question="When?", missing_fields=["deadline"], confidence=0.7,
    )
    registry = PendingDraftRegistry(ttl_seconds=1800)
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(needs_input),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=registry,
        tasks_db=_FakeTasksDb(),
    )
    await dispatcher.dispatch(_Msg(content="do a thing", author_id="alice"))
    await dispatcher.dispatch(_Msg(content="do another", author_id="bob"))
    assert registry.get_by_thread("dm:alice") is not None
    assert registry.get_by_thread("dm:bob") is not None
