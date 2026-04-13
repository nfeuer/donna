"""Type definitions for the chat interface."""

from __future__ import annotations

import dataclasses
import enum


class ChatIntent(str, enum.Enum):
    TASK_QUERY = "task_query"
    TASK_ACTION = "task_action"
    AGENT_OUTPUT_QUERY = "agent_output_query"
    PLANNING = "planning"
    FREEFORM = "freeform"
    ESCALATION_REQUEST = "escalation_request"


class ChatSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclasses.dataclass(frozen=True)
class ChatResponse:
    """Response from the ConversationEngine."""

    text: str
    needs_escalation: bool = False
    escalation_reason: str | None = None
    estimated_cost: float | None = None
    suggested_actions: list[str] = dataclasses.field(default_factory=list)
    session_pinned_task_id: str | None = None
    pin_suggestion: dict[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class ChatSession:
    """Read-only projection of a chat session row."""

    id: str
    user_id: str
    channel: str
    status: str
    created_at: str
    last_activity: str
    expires_at: str
    message_count: int
    pinned_task_id: str | None = None
    summary: str | None = None


@dataclasses.dataclass(frozen=True)
class ChatMessage:
    """Read-only projection of a chat message row."""

    id: str
    session_id: str
    role: str
    content: str
    created_at: str
    intent: str | None = None
    tokens_used: int | None = None
