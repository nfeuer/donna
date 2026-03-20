"""SQLAlchemy models for Donna's task database.

All tables include user_id from day one for future multi-user support.
Schema changes require an Alembic migration — never modify tables manually.
See docs/task-system.md and docs/model-layer.md.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all Donna models."""

    pass


# === Enums ===


class TaskStatus(str, enum.Enum):
    BACKLOG = "backlog"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING_INPUT = "waiting_input"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskDomain(str, enum.Enum):
    PERSONAL = "personal"
    WORK = "work"
    FAMILY = "family"


class DeadlineType(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"
    NONE = "none"


class AgentStatus(str, enum.Enum):
    PENDING = "pending"
    GATHERING_REQUIREMENTS = "gathering_requirements"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETE = "complete"
    FAILED = "failed"


class InputChannel(str, enum.Enum):
    SMS = "sms"
    DISCORD = "discord"
    SLACK = "slack"
    APP = "app"
    EMAIL = "email"
    VOICE = "voice"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    COMPLETED = "completed"


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
    last_activity: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
