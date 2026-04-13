"""Tests for chat configuration loading."""

from pathlib import Path
import tempfile
import time

import yaml

from donna.chat.config import ChatConfig, load_chat_config, get_chat_config


class TestChatConfig:
    def test_defaults(self) -> None:
        config = ChatConfig()
        assert config.persona.mode == "donna"
        assert config.sessions.ttl_minutes == 120
        assert config.sessions.context_budget_tokens == 24000
        assert config.sessions.summary_on_close is True
        assert config.escalation.enabled is True
        assert config.escalation.auto_approve_under_usd == 0.0
        assert config.escalation.daily_budget_usd == 2.0
        assert config.escalation.model == "parser"
        assert config.intents.classify_model == "local_parser"
        assert config.intents.templates_dir == "prompts/chat"

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "chat": {
                "persona": {"mode": "neutral"},
                "escalation": {"daily_budget_usd": 5.0},
            }
        }
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_chat_config(tmp_path)
        assert config.persona.mode == "neutral"
        assert config.escalation.daily_budget_usd == 5.0
        # Defaults still apply for unspecified fields
        assert config.sessions.ttl_minutes == 120

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_chat_config(tmp_path)
        assert config.persona.mode == "donna"

    def test_get_chat_config_caches(self, tmp_path: Path) -> None:
        config_data = {"chat": {"persona": {"mode": "donna"}}}
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        c1 = get_chat_config(tmp_path)
        c2 = get_chat_config(tmp_path)
        # Same object returned within TTL
        assert c1 is c2

    def test_get_chat_config_reloads_after_ttl(self, tmp_path: Path) -> None:
        config_data = {"chat": {"persona": {"mode": "donna"}}}
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        c1 = get_chat_config(tmp_path, cache_ttl_s=0)
        # Write new config
        config_data["chat"]["persona"]["mode"] = "neutral"
        config_file.write_text(yaml.dump(config_data))
        c2 = get_chat_config(tmp_path, cache_ttl_s=0)
        assert c2.persona.mode == "neutral"
