"""ClaudeNoveltyJudge — Claude call for no-match escalations."""
from __future__ import annotations

import pytest

from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge, NoveltyVerdict


class _FakeRouter:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[str] = []

    async def complete(self, prompt, *, task_type, user_id, **kwargs):
        self.calls.append(task_type)
        return self._response, {"cost_usd": 0.002, "latency_ms": 800}


class _FakeDb:
    async def list_capabilities(self):
        return []


@pytest.mark.asyncio
async def test_judge_returns_automation_verdict_with_polling_suggestion() -> None:
    response = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"from": "jane@x.com"},
        "schedule": {"cron": "0 */1 * * *", "human_readable": "hourly"},
        "deadline": None,
        "alert_conditions": {"expression": "action_required_count > 0", "channels": ["discord_dm"]},
        "polling_interval_suggestion": "0 */1 * * *",
        "skill_candidate": True,
        "skill_candidate_reasoning": "Email triage is a reusable pattern.",
        "clarifying_question": None,
    }
    router = _FakeRouter(response)
    judge = ClaudeNoveltyJudge(model_router=router, database=_FakeDb())
    verdict = await judge.evaluate("when I get an email from jane@x.com, message me", user_id="u1")
    assert isinstance(verdict, NoveltyVerdict)
    assert verdict.intent_kind == "automation"
    assert verdict.trigger_type == "on_schedule"
    assert verdict.skill_candidate is True
    assert verdict.polling_interval_suggestion == "0 */1 * * *"
    assert router.calls == ["claude_novelty"]


@pytest.mark.asyncio
async def test_judge_marks_non_candidate() -> None:
    response = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"folder_path": "~/tax-prep"},
        "schedule": {"cron": "0 10 * * 0", "human_readable": "Sundays at 10am"},
        "deadline": None,
        "alert_conditions": None,
        "polling_interval_suggestion": None,
        "skill_candidate": False,
        "skill_candidate_reasoning": "Annual tax workflow — user-specific, low-frequency.",
        "clarifying_question": None,
    }
    router = _FakeRouter(response)
    judge = ClaudeNoveltyJudge(model_router=router, database=_FakeDb())
    verdict = await judge.evaluate("every Sunday review tax prep folder", user_id="u1")
    assert verdict.skill_candidate is False
