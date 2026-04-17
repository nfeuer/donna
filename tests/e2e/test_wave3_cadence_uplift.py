"""AS-W3.11 — cadence clamped to sandbox floor; uplifts on shadow_primary + trusted.

Flow:

1. User DMs "watch URL every 15 minutes".
2. ChallengerAgent returns an automation match at capability=product_watch
   with target cron ``*/15 * * * *``.
3. Dispatcher clamps active to the sandbox floor (12h -> ``0 */12 * * *``)
   because the seeded product_watch skill is at state=sandbox.
4. Approve -> automation row persisted with active = 12h cron.
5. Promote the skill to shadow_primary via SkillLifecycleManager.transition
   — CadenceReclamper (registered on after_state_change) recomputes active
   to the shadow_primary floor (1h -> ``0 * * * *``).
6. Promote to trusted — CadenceReclamper recomputes active to the target
   ``*/15 * * * *`` because the trusted floor (15m) is now <= the target.
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
async def test_cadence_clamped_and_uplifts_on_lifecycle(runtime) -> None:
    from donna.tasks.db_models import SkillState

    conn = runtime.db.connection

    # Starting state: seeded product_watch skill is at sandbox.
    cursor = await conn.execute(
        "SELECT id, state FROM skill WHERE capability_name = 'product_watch'"
    )
    skill_id, state = await cursor.fetchone()
    assert state == "sandbox"

    # Canned challenger response: product_watch match with a *very* fast
    # target cron that the sandbox floor will clamp.
    runtime.fake_claude.canned["challenge_task"] = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.95,
        "confidence": 0.95,
        "extracted_inputs": {"url": "https://shop.example/item"},
        "schedule": {"cron": "*/15 * * * *", "human_readable": "every 15 minutes"},
        "alert_conditions": {
            "all_of": [{"field": "triggers_alert", "op": "==", "value": True}],
        },
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }

    # Dispatch.
    result = await runtime.intent_dispatcher.dispatch(
        _Msg(content="watch https://shop.example/item every 15 minutes")
    )
    assert result.kind == "automation_confirmation_needed"
    draft = result.draft_automation
    assert draft is not None
    assert draft.target_cadence_cron == "*/15 * * * *"
    # Sandbox floor in config/automations.yaml is 43200s (12h).
    # 15m target < 12h floor -> active clamps to 0 */12 * * *.
    assert draft.active_cadence_cron == "0 */12 * * *"

    # Approve -> row exists with the clamped active cadence.
    automation_id = await runtime.creation_path.approve(
        draft, name="watch example item"
    )
    assert automation_id is not None

    row = await runtime.automation_repo.get(automation_id)
    assert row is not None
    assert row.target_cadence_cron == "*/15 * * * *"
    assert row.active_cadence_cron == "0 */12 * * *"

    # --- Promote sandbox -> shadow_primary -------------------------------
    # CadenceReclamper is registered on lifecycle.after_state_change (see
    # harness._build_wave3_intent_pipeline) so active_cadence_cron is
    # rewritten as a side-effect of the transition.
    lifecycle = runtime.skill_bundle.lifecycle_manager
    await lifecycle.transition(
        skill_id=skill_id,
        to_state=SkillState.SHADOW_PRIMARY,
        reason="human_approval",
        actor="user",
        actor_id="nick",
    )

    row = await runtime.automation_repo.get(automation_id)
    # shadow_primary floor is 3600s (1h). 15m target < 1h floor -> hourly.
    assert row.active_cadence_cron == "0 * * * *", (
        f"expected hourly uplift on shadow_primary, got {row.active_cadence_cron!r}"
    )
    # Target is immutable.
    assert row.target_cadence_cron == "*/15 * * * *"

    # --- Promote shadow_primary -> trusted -------------------------------
    await lifecycle.transition(
        skill_id=skill_id,
        to_state=SkillState.TRUSTED,
        reason="human_approval",
        actor="user",
        actor_id="nick",
    )

    row = await runtime.automation_repo.get(automation_id)
    # trusted floor is 900s (15m). 15m target == floor -> active == target.
    assert row.active_cadence_cron == "*/15 * * * *", (
        f"expected active to equal target on trusted, got {row.active_cadence_cron!r}"
    )
    assert row.target_cadence_cron == "*/15 * * * *"
