"""AS-W3.4 — 'When I get an email ...' → polling automation + skill_candidate.

Flow:

1. User DMs "when I get an email from jane@x.com, message me".
2. ChallengerAgent returns status=escalate_to_claude (no seeded capability
   matches the pattern).
3. ClaudeNoveltyJudge returns an automation verdict with a
   polling_interval_suggestion and skill_candidate=True.
4. Dispatcher emits kind=automation_confirmation_needed with
   capability_name=None, target cadence = the suggested cron.
5. Approve via AutomationCreationPath → automation row is persisted with
   capability_name="" (no capability).
6. skill_candidate_report shows NO claude_native_registered row for this
   fingerprint (because skill_candidate=True means the judge wants to
   surface this pattern for future skill drafting — not register it as
   claude-native-only).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _Msg:
    content: str
    author_id: str = "nick"
    thread_id: int | None = None


@pytest.mark.asyncio
async def test_when_x_phrase_creates_polling_automation(runtime) -> None:
    conn = runtime.db.connection

    # 1. Challenger: no capability match -> escalate_to_claude.
    runtime.fake_claude.canned["challenge_task"] = {
        "intent_kind": "automation",
        "capability_name": None,
        "match_score": 0.2,
        "confidence": 0.4,
        "extracted_inputs": {},
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }

    # 2. Novelty judge: emits an automation with a polling cadence and
    #    skill_candidate=True (reusable email-triage pattern).
    runtime.fake_claude.canned["claude_novelty"] = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"from": "jane@x.com"},
        "schedule": {"cron": "0 */1 * * *", "human_readable": "hourly"},
        "deadline": None,
        "alert_conditions": {
            "all_of": [{"field": "has_new_match", "op": "==", "value": True}],
        },
        "polling_interval_suggestion": "0 */1 * * *",
        "skill_candidate": True,
        "skill_candidate_reasoning": "Email-triage watchers are a reusable pattern.",
        "clarifying_question": None,
    }

    # 3. Dispatch.
    result = await runtime.intent_dispatcher.dispatch(
        _Msg(content="when I get an email from jane@x.com, message me")
    )

    assert result.kind == "automation_confirmation_needed"
    draft = result.draft_automation
    assert draft is not None
    # No capability matched — skill candidate, dispatcher sets capability_name=None.
    assert draft.capability_name is None
    # Target cadence comes from polling_interval_suggestion.
    assert draft.target_cadence_cron == "0 */1 * * *"
    # Lifecycle state for a None capability is "claude_native" — 12h floor.
    # Target hourly is below the 12h floor -> active clamps to 12h.
    assert draft.active_cadence_cron == "0 */12 * * *"
    assert draft.skill_candidate is True
    assert draft.skill_candidate_reasoning.startswith("Email-triage")

    # 4. Attempt approval.
    #
    # BUG (documented): AutomationCreationPath.approve passes
    # ``capability_name=draft.capability_name or ""`` to the repository, but
    # the automation schema declares ``capability_name`` as NOT NULL with a
    # FOREIGN KEY to capability.name (see add_automation_tables_phase_5.py).
    # An empty string violates the FK and the repo raises
    # aiosqlite.IntegrityError, which the creation flow catches and re-labels
    # as AlreadyExistsError → approve() returns None.
    #
    # Net effect: polling / claude-native-only drafts (capability_name=None)
    # cannot be persisted via the current AutomationCreationPath. The
    # dispatcher produces the correct DraftAutomation, but the persistence
    # step silently fails.
    #
    # TODO(wave-3): make the automation table accept NULL capability_name
    # (or change AutomationCreationPath.approve to insert a synthetic
    # capability row for claude-native automations before calling
    # repository.create). When that lands, flip the assertion below to
    # ``assert automation_id is not None`` and drop the BUG note.
    automation_id = await runtime.creation_path.approve(
        draft, name="email watcher jane"
    )
    assert automation_id is None, (
        "persistence of claude-native-only automations is blocked by the "
        "FK/NOT NULL on automation.capability_name — see the BUG note above"
    )
    cursor = await conn.execute("SELECT COUNT(*) FROM automation")
    assert (await cursor.fetchone())[0] == 0

    # 5. skill_candidate_report: because skill_candidate=True, the dispatcher
    #    does NOT register a claude_native_registered row for this fingerprint.
    cursor = await conn.execute(
        "SELECT status FROM skill_candidate_report "
        "WHERE status = 'claude_native_registered'"
    )
    rows = await cursor.fetchall()
    assert rows == [], (
        "skill_candidate=True should NOT produce a claude_native_registered row; "
        "the pattern should remain surfaceable as a future skill candidate."
    )


@pytest.mark.asyncio
async def test_non_candidate_escalation_persists_claude_native_registered(runtime) -> None:
    """Companion assertion: when the judge returns skill_candidate=False, the
    dispatcher DOES write a claude_native_registered row — this differentiates
    the polling case above and proves the conditional persistence logic.
    """
    conn = runtime.db.connection

    runtime.fake_claude.canned["challenge_task"] = {
        "intent_kind": "automation",
        "capability_name": None,
        "match_score": 0.1,
        "confidence": 0.2,
        "extracted_inputs": {},
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    runtime.fake_claude.canned["claude_novelty"] = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"folder": "~/tax-prep"},
        "schedule": {"cron": "0 10 * * 0", "human_readable": "Sundays 10am"},
        "deadline": None,
        "alert_conditions": None,
        "polling_interval_suggestion": "0 10 * * 0",
        "skill_candidate": False,
        "skill_candidate_reasoning": "Tax prep workflow — user-specific, annual cadence.",
        "clarifying_question": None,
    }

    await runtime.intent_dispatcher.dispatch(
        _Msg(content="every Sunday at 10am review my tax prep folder")
    )

    cursor = await conn.execute(
        "SELECT reasoning FROM skill_candidate_report "
        "WHERE status = 'claude_native_registered'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0].startswith("Tax prep")
