"""AutomationConfirmationView — embed rendering + button callbacks."""
from __future__ import annotations

from donna.integrations.discord_views import AutomationConfirmationView
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


def _draft(active_cron: str = "0 */12 * * *") -> DraftAutomation:
    return DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt", "max_price_usd": 100},
        schedule_cron="*/15 * * * *",
        schedule_human="every 15 minutes",
        alert_conditions={
            "expression": "triggers_alert == true",
            "channels": ["discord_dm"],
        },
        target_cadence_cron="*/15 * * * *",
        active_cadence_cron=active_cron,
    )


def test_embed_shows_fields() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    text = "\n".join(field.value for field in embed.fields)
    assert "https://x.com/shirt" in text
    assert "every 15 minutes" in text


def test_embed_flags_clamped_cadence() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    text = "\n".join(field.value for field in embed.fields)
    assert "every 12 hours" in text  # active cadence surfaced
    assert "every 15 minutes" in text  # user's target preserved


def test_embed_without_clamp_shows_single_schedule() -> None:
    # target == active: should not emit the clamped dual-line message.
    draft = _draft(active_cron="*/15 * * * *")
    view = AutomationConfirmationView(draft=draft, name="watch shirt")
    embed = view.build_embed()
    schedule_fields = [f for f in embed.fields if f.name == "Schedule"]
    assert len(schedule_fields) == 1
    assert "Your target" not in schedule_fields[0].value


def test_embed_shows_alert_expression() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    text = "\n".join(field.value for field in embed.fields)
    assert "triggers_alert == true" in text


def test_embed_title_includes_name() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    assert "watch shirt" in embed.title
