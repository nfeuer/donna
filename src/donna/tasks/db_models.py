"""SQLAlchemy models for Donna's task database.

All tables include user_id from day one for future multi-user support.
Schema changes require an Alembic migration — never modify tables manually.
See docs/task-system.md and docs/model-layer.md.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all Donna models."""

    pass


# === Enums ===


class TaskStatus(enum.StrEnum):
    BACKLOG = "backlog"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING_INPUT = "waiting_input"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskDomain(enum.StrEnum):
    PERSONAL = "personal"
    WORK = "work"
    FAMILY = "family"


class DeadlineType(enum.StrEnum):
    HARD = "hard"
    SOFT = "soft"
    NONE = "none"


class AgentStatus(enum.StrEnum):
    PENDING = "pending"
    GATHERING_REQUIREMENTS = "gathering_requirements"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETE = "complete"
    FAILED = "failed"


class InputChannel(enum.StrEnum):
    SMS = "sms"
    DISCORD = "discord"
    SLACK = "slack"
    APP = "app"
    EMAIL = "email"
    VOICE = "voice"


class ConversationStatus(enum.StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    COMPLETED = "completed"


class TriggerType(enum.StrEnum):
    ON_MESSAGE = "on_message"
    ON_SCHEDULE = "on_schedule"
    ON_MANUAL = "on_manual"


class SkillState(enum.StrEnum):
    CLAUDE_NATIVE = "claude_native"
    SKILL_CANDIDATE = "skill_candidate"
    DRAFT = "draft"
    SANDBOX = "sandbox"
    SHADOW_PRIMARY = "shadow_primary"
    TRUSTED = "trusted"
    FLAGGED_FOR_REVIEW = "flagged_for_review"
    DEGRADED = "degraded"


# === Models ===


class Task(Base):
    """Primary task table. See docs/task-system.md for field documentation."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[TaskDomain] = mapped_column(
        Enum(TaskDomain), nullable=False, default=TaskDomain.PERSONAL
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.BACKLOG, index=True
    )
    estimated_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline_type: Mapped[DeadlineType] = mapped_column(
        Enum(DeadlineType), nullable=False, default=DeadlineType.NONE
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recurrence: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dependencies: Mapped[str | None] = mapped_column(
        JSON, nullable=True
    )  # JSON array of UUID strings
    parent_task: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    prep_work_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    prep_work_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_status: Mapped[AgentStatus | None] = mapped_column(
        Enum(AgentStatus), nullable=True
    )
    tags: Mapped[str | None] = mapped_column(JSON, nullable=True)  # JSON array of strings
    notes: Mapped[str | None] = mapped_column(JSON, nullable=True)  # JSON array of strings
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    created_via: Mapped[InputChannel] = mapped_column(
        Enum(InputChannel), nullable=False, default=InputChannel.DISCORD
    )
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    donna_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    nudge_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Wave 3: capability matched by intent dispatcher (nullable — claude-native)
    capability_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wave 3: JSON-serialized extracted inputs dict from the intent dispatcher
    inputs_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NudgeEvent(Base):
    """Persistent log of every nudge sent to the user. Supports stats tracking."""

    __tablename__ = "nudge_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, index=True
    )
    nudge_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "overdue", "reminder", "escalation"
    channel: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "discord", "sms", "email"
    escalation_tier: Mapped[int] = mapped_column(Integer, default=1)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    llm_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class InvocationLog(Base):
    """Structured log of every LLM call. See docs/model-layer.md Section 4.3."""

    __tablename__ = "invocation_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    model_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    model_actual: Mapped[str] = mapped_column(String(200), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    output: Mapped[str | None] = mapped_column(JSON, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    eval_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    spot_check_queued: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    skill_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )


class CorrectionLog(Base):
    """Logs user corrections for preference learning. See docs/preferences.md."""

    __tablename__ = "correction_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    field_corrected: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    rule_extracted: Mapped[str | None] = mapped_column(String(36), nullable=True)


class LearnedPreference(Base):
    """Extracted preference rules. See docs/preferences.md."""

    __tablename__ = "learned_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    condition: Mapped[str | None] = mapped_column(JSON, nullable=True)
    action: Mapped[str | None] = mapped_column(JSON, nullable=True)
    supporting_corrections: Mapped[str | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CalendarMirror(Base):
    """Local SQLite mirror of Google Calendar events.

    Used by CalendarSync to detect changes between poll cycles.
    One row per calendar event. Updated on every sync pass.
    See docs/scheduling.md.
    """

    __tablename__ = "calendar_mirror"

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    calendar_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    donna_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    donna_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    etag: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_synced: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # Slice 15: JSON-encoded list[{name, email}] captured from the
    # Google Calendar API so the meeting-note skill can render wikilinks.
    attendees: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConversationContext(Base):
    """Tracks multi-turn interactions on channels without threads. See docs/notifications.md."""

    __tablename__ = "conversation_context"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    questions_asked: Mapped[str | None] = mapped_column(JSON, nullable=True)
    responses_received: Mapped[str | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus), nullable=False, default=ConversationStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hard_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class EscalationState(Base):
    """Tracks per-task notification escalation tier state. See docs/notifications.md."""

    __tablename__ = "escalation_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_title: Mapped[str] = mapped_column(String(500), nullable=False)
    current_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    next_escalation_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ChatSessionStatus(enum.StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class ChatMessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatSessionModel(Base):
    """Chat conversation session.

    See ``docs/superpowers/specs/archive/2026-04-12-chat-interface-design.md``.
    """

    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    pinned_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    status: Mapped[ChatSessionStatus] = mapped_column(
        Enum(ChatSessionStatus), nullable=False, default=ChatSessionStatus.ACTIVE
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_activity: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChatMessageModel(Base):
    """Individual message in a chat session."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversation_sessions.id"), nullable=False, index=True
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(ChatMessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class Capability(Base):
    """Defines what a skill can do. See docs/skills-system.md."""

    __tablename__ = "capability"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False, index=True)
    default_output_shape: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Skill(Base):
    """Represents a skill implementation for a capability. See docs/skills-system.md."""

    __tablename__ = "skill"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_name: Mapped[str] = mapped_column(
        String(200), ForeignKey("capability.name"), nullable=False, unique=True,
    )
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[SkillState] = mapped_column(Enum(SkillState), nullable=False, index=True)
    requires_human_gate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillVersion(Base):
    """Version history for a skill implementation. See docs/skills-system.md."""

    __tablename__ = "skill_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_backbone: Mapped[str] = mapped_column(Text, nullable=False)
    step_content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schemas: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillStateTransition(Base):
    """Audit log of skill state changes. See docs/skills-system.md."""

    __tablename__ = "skill_state_transition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True,
    )
    from_state: Mapped[str] = mapped_column(String(30), nullable=False)
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkillRun(Base):
    __tablename__ = "skill_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True,
    )
    skill_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill_version.id"), nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    state_object: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tool_result_cache: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillStepResult(Base):
    __tablename__ = "skill_step_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill_run.id"), nullable=False, index=True,
    )
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    invocation_log_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillFixture(Base):
    __tablename__ = "skill_fixture"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True,
    )
    case_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_output_shape: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    captured_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillDivergence(Base):
    """Shadow-run divergence record for a skill_run. See docs/skills-system.md."""

    __tablename__ = "skill_divergence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill_run.id"), nullable=False, index=True,
    )
    shadow_invocation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    overall_agreement: Mapped[float] = mapped_column(Float, nullable=False)
    diff_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    flagged_for_evolution: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )


class SkillCandidateReport(Base):
    """Candidate skill report surfaced by the divergence analyser. See docs/skills-system.md."""

    __tablename__ = "skill_candidate_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    task_pattern_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_savings_usd: Mapped[float] = mapped_column(Float, nullable=False)
    volume_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    variance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkillEvolutionLog(Base):
    """Audit log of skill evolution events (diagnosis + rewrite attempts).

    See docs/skills-system.md.
    """

    __tablename__ = "skill_evolution_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True,
    )
    from_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    to_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(30), nullable=False)
    claude_invocation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    diagnosis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    targeted_case_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_results: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Automation(Base):
    """Recurring work item Donna runs on a schedule. See docs/skills-system.md §6.9."""

    __tablename__ = "automation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capability_name: Mapped[str] = mapped_column(
        String(200), ForeignKey("capability.name"), nullable=False, index=True,
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    alert_conditions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    alert_channels: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    max_cost_per_run_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_via: Mapped[str] = mapped_column(String(20), nullable=False)


class AutomationRun(Base):
    """Single execution of an automation. See docs/skills-system.md §5.12."""

    __tablename__ = "automation_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("automation.id"), nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    execution_path: Mapped[str] = mapped_column(String(20), nullable=False)
    skill_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invocation_log_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    alert_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


# === Slice 13: semantic memory ===


class MemoryDocument(Base):
    """A single ingested document (a vault note, chat turn, etc.).

    ``(user_id, source_type, source_id)`` is unique. Soft-deleted via
    ``deleted_at``; search joins filter on ``deleted_at IS NULL`` so
    stale embeddings in ``vec_memory_chunks`` never surface.
    """

    __tablename__ = "memory_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemoryChunk(Base):
    """One embedding-bearing slice of a :class:`MemoryDocument`.

    The embedding vector lives in the ``vec_memory_chunks`` sqlite-vec
    virtual table (no ORM model — raw SQL only). Chunks here are the
    payload surfaced by ``MemoryStore.search``.
    """

    __tablename__ = "memory_chunks"

    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memory_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
