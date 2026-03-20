"""Configuration loader for Donna.

Reads YAML config files and provides typed access. All extensible behavior
(model routing, task types, state machine, preferences) is driven by config,
not hardcoded in application logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """A single model alias definition."""

    provider: str
    model: str
    estimated_cost_per_1k_tokens: float | None = None


class RoutingEntry(BaseModel):
    """Routing config for a single task type."""

    model: str
    fallback: str | None = None
    confidence_threshold: float | None = None
    shadow: str | None = None


class CostConfig(BaseModel):
    """Budget thresholds."""

    monthly_budget_usd: float = 100.0
    daily_pause_threshold_usd: float = 20.0
    task_approval_threshold_usd: float = 5.0
    monthly_warning_pct: float = 0.90


class QualityMonitoringConfig(BaseModel):
    """Spot-check quality monitoring settings."""

    spot_check_rate: float = 0.05
    judge_model: str = "reasoner"
    judge_batch_schedule: str = "weekly"
    flag_threshold: float = 0.7
    enabled: bool = False


class ModelsConfig(BaseModel):
    """Top-level models configuration."""

    models: dict[str, ModelConfig]
    routing: dict[str, RoutingEntry]
    cost: CostConfig = Field(default_factory=CostConfig)
    quality_monitoring: QualityMonitoringConfig = Field(
        default_factory=QualityMonitoringConfig
    )


class TaskTypeEntry(BaseModel):
    """A single task type definition."""

    description: str
    model: str
    prompt_template: str
    output_schema: str
    tools: list[str] = Field(default_factory=list)
    shadow: str | None = None


class TaskTypesConfig(BaseModel):
    """Top-level task types configuration."""

    task_types: dict[str, TaskTypeEntry]


class TransitionEntry(BaseModel):
    """A single state machine transition."""

    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")
    trigger: str
    side_effects: list[str] = Field(default_factory=list)
    timeout_days: int | None = None

    model_config = {"populate_by_name": True}


class InvalidTransitionEntry(BaseModel):
    """An explicitly invalid transition."""

    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")
    reason: str
    except_states: list[str] = Field(default_factory=list, alias="except")

    model_config = {"populate_by_name": True}


class StateMachineConfig(BaseModel):
    """Task lifecycle state machine configuration."""

    states: list[str]
    initial_state: str
    transitions: list[TransitionEntry]
    invalid_transitions: list[InvalidTransitionEntry] = Field(default_factory=list)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def load_models_config(config_dir: Path) -> ModelsConfig:
    """Load model routing configuration."""
    data = load_yaml(config_dir / "donna_models.yaml")
    return ModelsConfig(**data)


def load_task_types_config(config_dir: Path) -> TaskTypesConfig:
    """Load task type registry."""
    data = load_yaml(config_dir / "task_types.yaml")
    return TaskTypesConfig(**data)


def load_state_machine_config(config_dir: Path) -> StateMachineConfig:
    """Load task lifecycle state machine."""
    data = load_yaml(config_dir / "task_states.yaml")
    return StateMachineConfig(**data)


# === Calendar / Scheduling Config ===


class TimeWindowConfig(BaseModel):
    """A single scheduling time window (start/end hour + allowed weekdays)."""

    start_hour: int
    end_hour: int
    days: list[int] = Field(default_factory=list)


class TimeWindowsConfig(BaseModel):
    """All named scheduling windows from calendar.yaml."""

    blackout: TimeWindowConfig
    quiet_hours: TimeWindowConfig
    work: TimeWindowConfig
    personal: TimeWindowConfig
    weekend: TimeWindowConfig


class CalendarEntryConfig(BaseModel):
    """Config for one Google Calendar (ID + access level)."""

    calendar_id: str
    access: str  # "read_write" or "read_only"


class SyncConfig(BaseModel):
    """Polling and sync window settings."""

    poll_interval_seconds: int = 300
    lookahead_days: int = 7
    lookbehind_days: int = 1


class SchedulingConfig(BaseModel):
    """Slot-search parameters."""

    slot_step_minutes: int = 15
    default_duration_minutes: int = 60
    search_horizon_days: int = 14


class CredentialsConfig(BaseModel):
    """OAuth2 credential file paths and scopes."""

    client_secrets_path: str = "credentials.json"
    token_path: str = "token.json"
    scopes: list[str]


class CalendarConfig(BaseModel):
    """Top-level calendar integration configuration."""

    calendars: dict[str, CalendarEntryConfig]
    sync: SyncConfig = Field(default_factory=SyncConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    time_windows: TimeWindowsConfig
    credentials: CredentialsConfig


def load_calendar_config(config_dir: Path) -> CalendarConfig:
    """Load calendar integration and scheduling configuration."""
    data = load_yaml(config_dir / "calendar.yaml")
    return CalendarConfig(**data)


# === SMS / Twilio Config ===


class SmsRateLimitConfig(BaseModel):
    """Outbound SMS rate limit settings."""

    max_per_day: int = 10


class SmsEscalationConfig(BaseModel):
    """Escalation tier wait times."""

    tier1_wait_minutes: int = 30
    tier2_wait_minutes: int = 60
    tier3_wait_minutes: int = 120
    busy_backoff_hours: int = 2


class SmsBlackoutConfig(BaseModel):
    """Hours during which SMS is completely blocked."""

    start_hour: int = 0
    end_hour: int = 6


class SmsConversationContextConfig(BaseModel):
    """TTL settings for SMS conversation contexts."""

    sliding_ttl_hours: int = 24
    hard_cap_hours: int = 72


class SmsConfig(BaseModel):
    """Top-level SMS integration configuration."""

    rate_limit: SmsRateLimitConfig = Field(default_factory=SmsRateLimitConfig)
    escalation: SmsEscalationConfig = Field(default_factory=SmsEscalationConfig)
    blackout: SmsBlackoutConfig = Field(default_factory=SmsBlackoutConfig)
    conversation_context: SmsConversationContextConfig = Field(
        default_factory=SmsConversationContextConfig
    )


def load_sms_config(config_dir: Path) -> SmsConfig:
    """Load SMS integration configuration."""
    data = load_yaml(config_dir / "sms.yaml")
    return SmsConfig(**data)


# === Email / Gmail Config ===


class EmailCredentialsConfig(BaseModel):
    """OAuth2 credential file paths and scopes for Gmail."""

    client_secrets_path: str = "credentials_gmail.json"
    token_path: str = "token_gmail.json"
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ]
    )


class EmailDigestConfig(BaseModel):
    """Digest schedule settings for email."""

    morning_hour: int = 6
    morning_minute: int = 30
    eod_hour: int = 17
    eod_minute: int = 30
    eod_weekdays_only: bool = True


class EmailConfig(BaseModel):
    """Top-level email integration configuration."""

    send_enabled: bool = False
    monitor_alias: str = ""
    user_email: str = ""
    credentials: EmailCredentialsConfig = Field(default_factory=EmailCredentialsConfig)
    digest: EmailDigestConfig = Field(default_factory=EmailDigestConfig)


def load_email_config(config_dir: Path) -> EmailConfig:
    """Load email integration configuration."""
    data = load_yaml(config_dir / "email.yaml")
    return EmailConfig(**data)
