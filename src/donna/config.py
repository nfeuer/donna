"""Configuration loader for Donna.

Reads YAML config files and provides typed access. All extensible behavior
(model routing, task types, state machine, preferences) is driven by config,
not hardcoded in application logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """A single model alias definition."""

    provider: str
    model: str
    estimated_cost_per_1k_tokens: float | None = None
    num_ctx: int | None = None


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


class OllamaConfig(BaseModel):
    """Connection settings for the local Ollama LLM server."""

    base_url: str = "http://localhost:11434"
    timeout_s: int = 120
    keepalive: str = "5m"
    default_num_ctx: int = 8192
    default_output_reserve: int = 1024


class ModelsConfig(BaseModel):
    """Top-level models configuration."""

    models: dict[str, ModelConfig]
    routing: dict[str, RoutingEntry]
    cost: CostConfig = Field(default_factory=CostConfig)
    quality_monitoring: QualityMonitoringConfig = Field(
        default_factory=QualityMonitoringConfig
    )
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


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
        return cast(dict[str, Any], yaml.safe_load(f))


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


class PriorityConfig(BaseModel):
    """Priority escalation thresholds for daily recalculation."""

    deadline_warning_days: int = 3
    deadline_critical_days: int = 1
    workload_threshold_per_day: int = 5
    escalation_after_reschedules: int = 1


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
    priority: PriorityConfig = Field(default_factory=PriorityConfig)


class PreferenceScheduleConfig(BaseModel):
    """Schedule and thresholds for the preference rule extraction batch job."""

    extract_interval_days: int = 7
    min_corrections_to_extract: int = 3
    min_confidence: float = 0.7
    max_corrections_per_batch: int = 50


class PreferencesConfig(BaseModel):
    """Top-level preferences configuration."""

    schedule: PreferenceScheduleConfig = Field(default_factory=PreferenceScheduleConfig)
    rules: list[dict[str, Any]] = Field(default_factory=list)


def load_preferences_config(config_dir: Path) -> PreferencesConfig:
    """Load preferences configuration."""
    data = load_yaml(config_dir / "preferences.yaml")
    return PreferencesConfig(**data)


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
    tier4_enabled: bool = False
    tier4_priority_threshold: int = 5
    tier4_max_per_day: int = 1


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


# === Memory / Vault Config (slice 12) ===
#
# The vault + safety blocks are consumed in slice 12. The embedding, retrieval,
# and sources blocks are parseable but unused — they land in slice 13+ and
# must round-trip through MemoryConfig without raising.


class VaultConfig(BaseModel):
    """Obsidian vault root + sync + git author."""

    root: str = "/donna/vault"
    git_author_name: str = "Donna"
    git_author_email: str = "donna@homelab.local"
    sync_method: str = "webdav"  # webdav | syncthing | manual
    templates_dir: str = "prompts/vault"
    ignore_globs: list[str] = Field(
        default_factory=lambda: [".obsidian/**", ".trash/**", ".git/**"]
    )


class VaultSafetyConfig(BaseModel):
    """Write-side guardrails enforced by VaultWriter.

    - max_note_bytes caps a single write payload.
    - path_allowlist is the set of top-level folders created on startup and
      accepted as write targets. Writes outside these prefixes are rejected.
    - sensitive_frontmatter_key: if present (truthy) on a note, writers must
      refuse to overwrite via agent tools. Reserved for slice 14+.
    """

    max_note_bytes: int = 200_000
    path_allowlist: list[str] = Field(
        default_factory=lambda: [
            "Inbox",
            "Meetings",
            "People",
            "Projects",
            "Daily",
            "Reviews",
        ]
    )
    sensitive_frontmatter_key: str = "donna_sensitive"


class VaultEmbeddingConfig(BaseModel):
    """Embedding provider + chunking parameters (slice 13).

    `provider` selects the EmbeddingProvider factory branch. `version_tag`
    stamps every chunk; bumping it triggers reindexing on the next
    backfill. `dim` must match the provider output and the sqlite-vec
    virtual table column. `max_tokens` / `chunk_overlap` drive the
    chunker.
    """

    provider: str = "minilm-l6-v2"
    version_tag: str = "minilm-l6-v2@2024-05"
    dim: int = 384
    max_tokens: int = 256
    chunk_overlap: int = 32
    # Legacy slice-12 aliases — still parseable so old configs boot.
    # Prefer `max_tokens` over `chunk_tokens`; `model` is ignored (the
    # provider branch is selected by `provider`).
    model: str | None = None
    chunk_tokens: int | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


class VaultRetrievalConfig(BaseModel):
    """Retrieval knobs (slice 13)."""

    default_k: int = 8
    min_score: float = 0.25
    max_k: int = 32
    # Legacy slice-12 alias.
    top_k: int | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


class VaultSourceConfig(BaseModel):
    """Per-source ingestion knobs. Vault is the only source in slice 13.

    Slice 16 adds ``rename_window_seconds`` controlling the in-memory
    TTL buffer used by :class:`donna.memory.sources_vault.VaultSource`
    to pair ``deleted`` + ``added`` events as a rename (and skip the
    re-embed) rather than a delete + upsert.
    """

    enabled: bool = True
    chunker: str = "markdown_heading"
    ignore_globs: list[str] = Field(default_factory=list)
    rename_window_seconds: float = 2.0


class ChatSourceConfig(BaseModel):
    """Chat-turn ingestion knobs (slice 14).

    ``index_roles`` filters which message roles contribute to turns.
    ``min_chars`` drops short messages unless they contain a question
    mark or a configured task verb. ``task_verbs`` is the allowlist
    that rescues short imperative messages from the filter.
    """

    enabled: bool = False
    index_roles: list[str] = Field(default_factory=lambda: ["user", "assistant"])
    min_chars: int = 20
    merge_consecutive_same_role: bool = True
    task_verbs: list[str] = Field(
        default_factory=lambda: [
            "do", "call", "email", "schedule", "remind",
            "send", "book", "buy", "check", "review",
        ]
    )
    chunker: str = "chat_turn"

    model_config = {"populate_by_name": True, "extra": "ignore"}


class TaskSourceConfig(BaseModel):
    """Task mutation ingestion knobs (slice 14).

    ``reindex_on_status`` enumerates the terminal statuses that force a
    re-embed even when the semantic content hash is unchanged (the
    final-state context is high-signal for retrieval).
    """

    enabled: bool = False
    reindex_on_status: list[str] = Field(
        default_factory=lambda: ["done", "cancelled"]
    )
    chunker: str = "task"

    model_config = {"populate_by_name": True, "extra": "ignore"}


class CorrectionSourceConfig(BaseModel):
    """Correction log ingestion knobs (slice 14)."""

    enabled: bool = False

    model_config = {"populate_by_name": True, "extra": "ignore"}


def _coerce_source_toggle(value: Any, model: type[BaseModel]) -> BaseModel:
    """Allow legacy ``chat: false`` style configs to still parse."""
    if isinstance(value, bool):
        return model(enabled=value)
    if isinstance(value, dict):
        return model(**value)
    if isinstance(value, model):
        return value
    raise TypeError(f"Unsupported source config value: {value!r}")


class VaultSourcesConfig(BaseModel):
    """Episodic source toggles.

    Slice 13 wired ``vault``. Slice 14 adds ``chat`` / ``task`` /
    ``correction`` as full config blocks. Legacy configs that set
    ``chat: false`` (slice-13 stubs) still parse via the
    ``field_validator`` below.
    """

    vault: VaultSourceConfig = Field(default_factory=VaultSourceConfig)
    chat: ChatSourceConfig = Field(default_factory=ChatSourceConfig)
    task: TaskSourceConfig = Field(default_factory=TaskSourceConfig)
    correction: CorrectionSourceConfig = Field(default_factory=CorrectionSourceConfig)

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> VaultSourcesConfig:
        # Accept legacy keys: `tasks` → `task`, `corrections` → `correction`,
        # plain bools for any of chat/task/correction.
        if isinstance(obj, dict):
            data: dict[str, Any] = dict(obj)
            if "tasks" in data and "task" not in data:
                data["task"] = data.pop("tasks")
            if "corrections" in data and "correction" not in data:
                data["correction"] = data.pop("corrections")
            for key, model in (
                ("chat", ChatSourceConfig),
                ("task", TaskSourceConfig),
                ("correction", CorrectionSourceConfig),
            ):
                if key in data:
                    data[key] = _coerce_source_toggle(data[key], model)
            obj = data
        return super().model_validate(obj, *args, **kwargs)


class MeetingNoteContextLimits(BaseModel):
    """Per-category caps on memory hits folded into the LLM prompt."""

    prior_meetings: int = 5
    recent_chats: int = 5
    open_tasks: int = 5


class MeetingNoteSkillConfig(BaseModel):
    """Slice 15: meeting-note autowrite skill.

    ``autonomy_level`` here is per-template and overrides the agent-level
    autonomy in ``agents.yaml`` for purposes of path redirection. ``low``
    forces writes into ``Inbox/``; ``medium`` / ``high`` honour the
    caller-computed target path.
    """

    enabled: bool = True
    poll_interval_seconds: int = 60
    lookback_minutes: int = 5
    autonomy_level: Literal["low", "medium", "high"] = "medium"
    context_limits: MeetingNoteContextLimits = Field(
        default_factory=MeetingNoteContextLimits
    )


class WeeklyReviewContextLimits(BaseModel):
    completed_tasks: int = 25
    meetings: int = 15
    commitments: int = 15
    chat_highlights: int = 10


class WeeklyReviewSkillConfig(BaseModel):
    """Slice 16 — weekly self-review scaffold written every Sunday."""

    enabled: bool = True
    hour_utc: int = 21
    minute_utc: int = 0
    day_of_week: int = 6  # Sunday (Mon=0..Sun=6)
    autonomy_level: Literal["low", "medium", "high"] = "medium"
    context_limits: WeeklyReviewContextLimits = Field(
        default_factory=WeeklyReviewContextLimits
    )


class DailyReflectionContextLimits(BaseModel):
    meetings: int = 10
    completed_tasks: int = 25
    chat_highlights: int = 10


class DailyReflectionSkillConfig(BaseModel):
    """Slice 16 — end-of-day reflection scaffold."""

    enabled: bool = True
    hour_utc: int = 21
    minute_utc: int = 0
    autonomy_level: Literal["low", "medium", "high"] = "medium"
    context_limits: DailyReflectionContextLimits = Field(
        default_factory=DailyReflectionContextLimits
    )


class PersonProfileContextLimits(BaseModel):
    vault_hits: int = 10
    chat_hits: int = 10
    task_hits: int = 10
    correction_hits: int = 5


class PersonProfileSkillConfig(BaseModel):
    """Slice 16 — weekly person-profile fill for ``People/`` notes.

    Two triggers share this skill: a mention-count threshold sweep and
    a stub-fill pass over short ``People/`` notes. Both run from the
    same weekly cron.
    """

    enabled: bool = True
    hour_utc: int = 22
    minute_utc: int = 0
    day_of_week: int = 6  # Sunday
    trigger_mentions_threshold: int = 3
    min_body_chars: int = 120
    lookback_days: int = 7
    autonomy_level: Literal["low", "medium", "high"] = "low"
    context_limits: PersonProfileContextLimits = Field(
        default_factory=PersonProfileContextLimits
    )


class CommitmentLogContextLimits(BaseModel):
    chat_hits: int = 50
    task_hits: int = 25


class CommitmentLogSkillConfig(BaseModel):
    """Slice 16 — daily commitment roll-up over chat + task sources."""

    enabled: bool = True
    hour_utc: int = 20
    minute_utc: int = 30
    autonomy_level: Literal["low", "medium", "high"] = "medium"
    context_limits: CommitmentLogContextLimits = Field(
        default_factory=CommitmentLogContextLimits
    )


class MemorySkillsConfig(BaseModel):
    """Autonomous template-write skills keyed by template name."""

    meeting_note: MeetingNoteSkillConfig = Field(
        default_factory=MeetingNoteSkillConfig
    )
    weekly_review: WeeklyReviewSkillConfig = Field(
        default_factory=WeeklyReviewSkillConfig
    )
    daily_reflection: DailyReflectionSkillConfig = Field(
        default_factory=DailyReflectionSkillConfig
    )
    person_profile: PersonProfileSkillConfig = Field(
        default_factory=PersonProfileSkillConfig
    )
    commitment_log: CommitmentLogSkillConfig = Field(
        default_factory=CommitmentLogSkillConfig
    )


class MemoryConfig(BaseModel):
    """Top-level memory/vault configuration."""

    vault: VaultConfig = Field(default_factory=VaultConfig)
    safety: VaultSafetyConfig = Field(default_factory=VaultSafetyConfig)
    embedding: VaultEmbeddingConfig = Field(default_factory=VaultEmbeddingConfig)
    retrieval: VaultRetrievalConfig = Field(default_factory=VaultRetrievalConfig)
    sources: VaultSourcesConfig = Field(default_factory=VaultSourcesConfig)
    skills: MemorySkillsConfig = Field(default_factory=MemorySkillsConfig)


def load_memory_config(config_dir: Path) -> MemoryConfig:
    """Load the memory/vault integration config from ``memory.yaml``."""
    data = load_yaml(config_dir / "memory.yaml")
    return MemoryConfig(**data)


# === Discord Config ===


class DiscordCommandsConfig(BaseModel):
    """Slash command settings."""

    enabled: bool = True
    sync_on_ready: bool = True


class EveningCheckinConfig(BaseModel):
    """Evening check-in prompt schedule."""

    enabled: bool = True
    hour: int = 19
    minute: int = 0


class StaleDetectionConfig(BaseModel):
    """Stale task detection settings."""

    enabled: bool = True
    stale_days: int = 7
    check_interval_hours: int = 24


class PostMeetingConfig(BaseModel):
    """Post-meeting capture prompt settings."""

    enabled: bool = True
    delay_minutes: int = 5


class AfternoonInactivityConfig(BaseModel):
    """Afternoon inactivity check settings."""

    enabled: bool = True
    hour: int = 14
    minute: int = 0


class ProactivePromptsConfig(BaseModel):
    """All proactive prompt schedules."""

    evening_checkin: EveningCheckinConfig = Field(
        default_factory=EveningCheckinConfig
    )
    stale_detection: StaleDetectionConfig = Field(
        default_factory=StaleDetectionConfig
    )
    post_meeting_capture: PostMeetingConfig = Field(
        default_factory=PostMeetingConfig
    )
    afternoon_inactivity: AfternoonInactivityConfig = Field(
        default_factory=AfternoonInactivityConfig
    )


class DiscordConfig(BaseModel):
    """Top-level Discord integration configuration."""

    commands: DiscordCommandsConfig = Field(default_factory=DiscordCommandsConfig)
    proactive_prompts: ProactivePromptsConfig = Field(
        default_factory=ProactivePromptsConfig
    )


def load_discord_config(config_dir: Path) -> DiscordConfig:
    """Load Discord integration configuration."""
    data = load_yaml(config_dir / "discord.yaml")
    return DiscordConfig(**data.get("discord", {}))


# === Skill System Config ===


class SkillSystemConfig(BaseModel):
    """Skill system runtime configuration (Phase 1–4)."""

    # Phase 1
    enabled: bool = False
    match_confidence_high: float = 0.75
    match_confidence_medium: float = 0.40
    similarity_audit_threshold: float = 0.80
    seed_skills_initial_state: str = "sandbox"

    # Phase 3 additions
    shadow_sample_rate_trusted: float = 0.05
    sandbox_promotion_min_runs: int = 20
    sandbox_promotion_validity_rate: float = 0.90
    shadow_primary_promotion_min_runs: int = 100
    shadow_primary_promotion_agreement_rate: float = 0.85
    degradation_rolling_window: int = 30
    degradation_ci_confidence: float = 0.95
    auto_draft_daily_cap: int = 50
    auto_draft_min_expected_savings_usd: float = 5.0
    auto_draft_fixture_pass_rate: float = 0.80
    nightly_run_hour_utc: int = 3
    degradation_agreement_threshold: float = 0.5

    # Phase 4 — evolution loop
    evolution_min_divergence_cases: int = 15
    evolution_max_divergence_cases: int = 30
    evolution_targeted_case_pass_rate: float = 0.80
    evolution_fixture_regression_pass_rate: float = 0.95
    evolution_recent_success_count: int = 20
    evolution_recent_success_window_days: int = 30
    evolution_max_consecutive_failures: int = 2
    evolution_estimated_cost_usd: float = 0.75
    evolution_daily_cap: int = 10

    # Phase 4 — correction clustering
    correction_cluster_window_runs: int = 10
    correction_cluster_threshold: int = 2

    # Phase 5 — automation subsystem
    automation_poll_interval_seconds: int = 15
    automation_min_interval_default_seconds: int = 300
    automation_failure_pause_threshold: int = 5
    automation_max_cost_per_run_default_usd: float = 2.0

    # Wave 1 — validation executor
    validation_per_step_timeout_s: int = 60
    validation_per_run_timeout_s: int = 300

    # Wave 5 — F-9: configurable window for baseline_agreement reset.
    baseline_reset_window: int = 100


def load_skill_system_config(config_dir: Path) -> SkillSystemConfig:
    """Load skill system configuration, falling back to defaults if missing."""
    path = config_dir / "skills.yaml"
    if not path.exists():
        return SkillSystemConfig()
    data = load_yaml(path) or {}
    return SkillSystemConfig(**data)
