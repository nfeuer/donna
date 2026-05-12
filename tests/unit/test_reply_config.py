"""Tests for reply handler config loading."""
from __future__ import annotations

from pathlib import Path

from donna.config import load_reply_actions_config, load_reply_intents_config


def test_load_reply_intents_config() -> None:
    config = load_reply_intents_config(Path("config"))
    assert "mark_done" in config.intents
    assert "reschedule" in config.intents
    assert "busy" in config.intents
    for name, intent in config.intents.items():
        assert len(intent.keywords) > 0
        assert intent.action


def test_load_reply_actions_config() -> None:
    config = load_reply_actions_config(Path("config"))
    assert "mark_done" in config.actions
    assert "create_task" in config.actions
    assert "request_capability" in config.actions
    for name, action in config.actions.items():
        assert action.description
        assert action.handler


def test_fast_path_config_defaults() -> None:
    config = load_reply_intents_config(Path("config"))
    assert config.fast_path.max_length == 60
    assert len(config.fast_path.multi_intent_signals) > 0


def test_memory_config_defaults() -> None:
    config = load_reply_actions_config(Path("config"))
    assert config.memory.window_size == 10
    assert config.memory.retention_days == 7
