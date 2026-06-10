from __future__ import annotations

from pathlib import Path

from donna.config import (
    NotificationPolicyConfig,
    load_notification_policy_config,
)


def test_load_notification_policy(tmp_path: Path) -> None:
    (tmp_path / "notifications.yaml").write_text(
        "notification_policy:\n"
        "  blackout_exempt: [reminder, debug]\n"
        "  quiet_exempt: [debug]\n"
    )
    cfg = load_notification_policy_config(tmp_path)
    assert isinstance(cfg, NotificationPolicyConfig)
    assert cfg.blackout_exempt == ["reminder", "debug"]
    assert cfg.quiet_exempt == ["debug"]


def test_load_notification_policy_missing_section_defaults_empty(tmp_path: Path) -> None:
    (tmp_path / "notifications.yaml").write_text("{}\n")
    cfg = load_notification_policy_config(tmp_path)
    assert cfg.blackout_exempt == []
    assert cfg.quiet_exempt == []
