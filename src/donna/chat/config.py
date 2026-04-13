"""Chat configuration — Pydantic models with hot-reload support.

Config is loaded from config/chat.yaml with a short TTL cache.
Edits via the admin dashboard take effect within seconds.
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    mode: str = "donna"  # "donna" | "neutral"
    template: str = "prompts/chat/chat_system.md"


class SessionsConfig(BaseModel):
    ttl_minutes: int = 120
    context_budget_tokens: int = 24000
    summary_on_close: bool = True


class EscalationConfig(BaseModel):
    enabled: bool = True
    auto_approve_under_usd: float = 0.0
    daily_budget_usd: float = 2.0
    model: str = "parser"


class IntentsConfig(BaseModel):
    classify_model: str = "local_parser"
    templates_dir: str = "prompts/chat"


class DiscordChatConfig(BaseModel):
    chat_channel_id: int | None = None


class ChatConfig(BaseModel):
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    intents: IntentsConfig = Field(default_factory=IntentsConfig)
    discord: DiscordChatConfig = Field(default_factory=DiscordChatConfig)


def load_chat_config(config_dir: Path) -> ChatConfig:
    """Load chat config from config/chat.yaml. Returns defaults if missing."""
    path = config_dir / "chat.yaml"
    if not path.exists():
        return ChatConfig()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    chat_data = raw.get("chat", {})
    return ChatConfig(**chat_data)


# Simple TTL cache for hot-reload
_cache: dict[str, tuple[float, ChatConfig]] = {}


def get_chat_config(
    config_dir: Path, cache_ttl_s: float = 5.0
) -> ChatConfig:
    """Get chat config with short TTL cache for hot-reload."""
    key = str(config_dir)
    now = time.monotonic()
    if key in _cache:
        cached_at, cached_config = _cache[key]
        if now - cached_at < cache_ttl_s:
            return cached_config
    config = load_chat_config(config_dir)
    _cache[key] = (now, config)
    return config
