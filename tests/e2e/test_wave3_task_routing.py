"""AS-W3.2 — 'get oil change by Wednesday' routes to the TASK path, not automation.

Flow:

1. User DMs a task phrase ("get oil change by Wednesday").
2. ChallengerAgent returns status=escalate_to_claude (no capability matches
   this phrase in the seed registry — product_watch is the only seeded
   capability).
3. ClaudeNoveltyJudge returns intent_kind=task with a populated deadline.
4. Dispatcher routes to ``_create_task_from_verdict`` and returns
   ``kind=task_created``.
5. No automation row is written; a task row is persisted with the deadline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest


@dataclass
class _Msg:
    content: str
    author_id: str = "nick"
    thread_id: int | None = None


@pytest.mark.asyncio
async def test_task_phrase_routes_to_task_not_automation(runtime) -> None:
    conn = runtime.db.connection

    # Snapshot the pre-dispatch automation / task counts so we can assert
    # the dispatcher did not create an automation row.
    cursor = await conn.execute("SELECT COUNT(*) FROM automation")
    automations_before = (await cursor.fetchone())[0]
    cursor = await conn.execute("SELECT COUNT(*) FROM tasks")
    tasks_before = (await cursor.fetchone())[0]

    # Challenger: no capability matches an oil-change request.
    runtime.fake_claude.canned["challenge_task"] = {
        "intent_kind": "task",
        "capability_name": None,
        "match_score": 0.1,
        "confidence": 0.3,
        "extracted_inputs": {},
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }

    # Novelty judge: task with a concrete deadline.
    deadline_iso = (
        datetime.now(UTC) + timedelta(days=3)
    ).replace(microsecond=0).isoformat()
    runtime.fake_claude.canned["claude_novelty"] = {
        "intent_kind": "task",
        "trigger_type": None,
        "extracted_inputs": {"vendor": "local mechanic"},
        "schedule": None,
        "deadline": deadline_iso,
        "alert_conditions": None,
        "polling_interval_suggestion": None,
        "skill_candidate": False,
        "skill_candidate_reasoning": "One-off errand — not a reusable pattern.",
        "clarifying_question": None,
    }

    # Dispatch.
    result = await runtime.intent_dispatcher.dispatch(
        _Msg(content="get oil change by Wednesday")
    )

    assert result.kind == "task_created"
    assert result.task_id is not None

    # No automation row was created.
    cursor = await conn.execute("SELECT COUNT(*) FROM automation")
    automations_after = (await cursor.fetchone())[0]
    assert automations_after == automations_before, (
        "task phrases must never write to the automation table"
    )

    # Task row exists and carries the deadline.
    cursor = await conn.execute("SELECT COUNT(*) FROM tasks")
    tasks_after = (await cursor.fetchone())[0]
    assert tasks_after == tasks_before + 1

    cursor = await conn.execute(
        "SELECT title, deadline FROM tasks WHERE id = ?",
        (result.task_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    title, stored_deadline = row
    assert "oil change" in title.lower()
    assert stored_deadline is not None, "deadline should be persisted"
    # Stored as ISO — parse back and compare (allow second-level tolerance).
    parsed = datetime.fromisoformat(stored_deadline)
    expected = datetime.fromisoformat(deadline_iso)
    assert abs((parsed - expected).total_seconds()) < 2
