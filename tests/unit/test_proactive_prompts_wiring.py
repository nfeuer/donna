"""Unit: the four proactive prompts are constructed from config and wired.

Regression guard for the 2026-07-02 audit finding — the prompts existed but
`_build_notification_tasks` never set the NotificationTasks fields, so the
server start-guards were always false and none of them ran.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from donna.cli_wiring import _build_proactive_prompts


def _fake_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.config_dir = Path("config")  # real repo config/discord.yaml
    ctx.notification_service = MagicMock()
    ctx.db = MagicMock()
    ctx.user_id = "nick"
    ctx.tz = None
    return ctx


def test_build_proactive_prompts_constructs_all_four_by_default():
    prompts = _build_proactive_prompts(_fake_ctx())
    assert prompts["post_meeting_capture"] is not None
    assert prompts["evening_checkin"] is not None
    assert prompts["stale_detector"] is not None
    assert prompts["afternoon_inactivity"] is not None


def test_build_proactive_prompts_respects_disabled(monkeypatch):
    import donna.config as config_mod
    from donna.config import (
        AfternoonInactivityConfig,
        DiscordConfig,
        EveningCheckinConfig,
        PostMeetingConfig,
        ProactivePromptsConfig,
        StaleDetectionConfig,
    )

    disabled = DiscordConfig(
        proactive_prompts=ProactivePromptsConfig(
            evening_checkin=EveningCheckinConfig(enabled=False),
            stale_detection=StaleDetectionConfig(enabled=False),
            post_meeting_capture=PostMeetingConfig(enabled=False),
            afternoon_inactivity=AfternoonInactivityConfig(enabled=False),
        )
    )
    # The helper does `from donna.config import load_discord_config` at call
    # time, so patching the source module attribute takes effect.
    monkeypatch.setattr(config_mod, "load_discord_config", lambda _dir: disabled)

    prompts = _build_proactive_prompts(_fake_ctx())
    assert all(v is None for v in prompts.values())
