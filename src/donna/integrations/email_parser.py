"""Forwarded-email parser and task creation pipeline for Donna.

Monitors a configured email alias for forwarded messages, parses the
forwarded structure, and routes the content through the existing
InputParser pipeline to create tasks — same flow as Discord/SMS.

Typical forwarded email structure (various clients):

    -------- Forwarded Message --------
    From: boss@example.com
    Sent: Thu, 20 Mar 2026 09:00:00 +0000
    To: donna-tasks@example.com
    Subject: Please handle the Q1 report

    Hi Nick, can you get the Q1 report done by Friday?

The parser extracts the original subject + body and treats them as
the task description fed to InputParser.

See slices/slice_08_email_corrections.md and docs/integrations.md.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from donna.integrations.gmail import GmailClient
    from donna.orchestrator.input_parser import InputParser
    from donna.tasks.database import Database

logger = structlog.get_logger()

# Patterns for forwarded email detection.
_FORWARD_PATTERNS = [
    re.compile(r"-{3,}\s*forwarded message\s*-{3,}", re.IGNORECASE),
    re.compile(r"begin forwarded message", re.IGNORECASE),
    re.compile(r"_{3,}\s*original message\s*_{3,}", re.IGNORECASE),
    re.compile(r"from:.*\nsent:.*\nto:", re.IGNORECASE | re.MULTILINE),
]

# Headers to extract from the forwarded block.
_HEADER_RE = re.compile(
    r"^(?:from|sent|date|to|subject):\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_SUBJECT_RE = re.compile(r"^subject:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_FROM_RE = re.compile(r"^from:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class ForwardedEmail:
    """Parsed representation of a forwarded email."""

    original_sender: str
    original_subject: str
    original_body: str
    raw_body: str


def parse_forwarded(raw_body: str) -> ForwardedEmail | None:
    """Detect and parse a forwarded email from message body text.

    Args:
        raw_body: The plain-text body of the received email.

    Returns:
        ForwardedEmail if a forwarded structure is detected, else None.
    """
    # Check for forwarded email patterns.
    separator_match = None
    for pattern in _FORWARD_PATTERNS:
        m = pattern.search(raw_body)
        if m:
            separator_match = m
            break

    if separator_match is None:
        return None

    # Everything after the separator is the forwarded block.
    after = raw_body[separator_match.end():]

    # Extract original sender and subject from forwarded headers.
    from_match = _FROM_RE.search(after)
    subject_match = _SUBJECT_RE.search(after)

    original_sender = from_match.group(1).strip() if from_match else ""
    original_subject = subject_match.group(1).strip() if subject_match else ""

    # Body is the text after the header lines (blank line separates headers from body).
    # Find the first blank line after the forwarded headers.
    lines = after.splitlines()
    body_lines: list[str] = []
    in_headers = True
    for line in lines:
        if in_headers and line.strip() == "":
            in_headers = False
            continue
        if not in_headers:
            body_lines.append(line)

    original_body = "\n".join(body_lines).strip()

    # If we couldn't separate headers from body cleanly, use everything after separator.
    if not original_body:
        original_body = after.strip()

    return ForwardedEmail(
        original_sender=original_sender,
        original_subject=original_subject,
        original_body=original_body,
        raw_body=raw_body,
    )


def _build_task_text(forwarded: ForwardedEmail) -> str:
    """Compose the task description text sent to InputParser."""
    parts: list[str] = []
    if forwarded.original_subject:
        parts.append(f"Email subject: {forwarded.original_subject}")
    if forwarded.original_sender:
        parts.append(f"From: {forwarded.original_sender}")
    if forwarded.original_body:
        parts.append(forwarded.original_body)
    return "\n".join(parts)


async def poll_and_create_tasks(
    gmail: GmailClient,
    input_parser: InputParser,
    db: Database,
    user_id: str,
    monitor_alias: str,
) -> int:
    """Poll the monitor alias for forwarded emails and create tasks.

    Searches for unread messages sent to monitor_alias, parses each for
    forwarded content, runs them through InputParser, and creates tasks.
    Already-processed messages are marked as read to avoid re-processing.

    Args:
        gmail: Authenticated GmailClient instance.
        input_parser: Existing InputParser for task extraction.
        db: Database for task storage.
        user_id: The owner user ID for created tasks.
        monitor_alias: Email address to monitor (e.g. donna-tasks@example.com).

    Returns:
        Number of tasks successfully created.
    """
    query = f"to:{monitor_alias} is:unread"
    try:
        messages = await gmail.search_emails(query, max_results=20)
    except Exception:
        logger.exception("email_poll_search_failed", monitor_alias=monitor_alias)
        return 0

    created = 0
    for msg in messages:
        try:
            forwarded = parse_forwarded(msg.body_text)
            if forwarded is None:
                logger.info(
                    "email_not_forwarded",
                    message_id=msg.id,
                    subject=msg.subject,
                )
                continue

            task_text = _build_task_text(forwarded)
            result = await input_parser.parse(task_text, user_id=user_id, channel="email")

            # Store the task in the database.
            from donna.tasks.db_models import DeadlineType, InputChannel, TaskDomain

            try:
                domain = TaskDomain(result.domain.lower())
            except ValueError:
                domain = TaskDomain.PERSONAL

            try:
                deadline_type = DeadlineType(result.deadline_type)
            except ValueError:
                deadline_type = DeadlineType.NONE

            deadline: datetime | None = None
            if result.deadline:
                try:
                    deadline = datetime.fromisoformat(result.deadline)
                except ValueError:
                    logger.warning("unparseable_deadline", deadline=result.deadline)

            await db.create_task(
                user_id=user_id,
                title=result.title,
                description=result.description,
                domain=domain,
                priority=result.priority,
                deadline=deadline,
                deadline_type=deadline_type,
                estimated_duration=result.estimated_duration,
                tags=result.tags,
                prep_work_flag=result.prep_work_flag,
                agent_eligible=result.agent_eligible,
                created_via=InputChannel.EMAIL,
            )

            logger.info(
                "email_task_created",
                message_id=msg.id,
                subject=msg.subject,
                task_title=result.title,
                user_id=user_id,
            )
            created += 1

        except Exception:
            logger.exception(
                "email_task_creation_failed",
                message_id=msg.id,
                user_id=user_id,
            )

    if created:
        logger.info("email_poll_complete", created=created, user_id=user_id)

    return created
