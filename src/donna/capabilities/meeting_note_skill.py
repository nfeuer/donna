"""Slice 15 — post-meeting note autowrite skill.

This is NOT a DSL skill under ``src/donna/skills/``; those are
user-corrected YAML skills. This is a Python-coded autonomous
*capability*: triggered by :class:`MeetingEndPoller` when a calendar
event just ended, it composes memory context, delegates to the shared
:class:`MemoryInformedWriter`, and produces a scaffold note under
``Meetings/{date}-{slug}.md``.

Flow (single ``run_for_event``):

1. Parse ``event.attendees`` JSON → list of ``{name, email}``.
2. Resolve each attendee name to a wikilink (existing ``People/{name}.md``
   → ``[[People/{name}]]``; else ``[[{name}]]``).
3. Fetch three memory-search categories concurrently, capped per config:
   - prior meetings (``source_type=vault``, post-filtered on
     ``metadata.type == 'meeting'``);
   - recent chats mentioning the attendees;
   - open tasks tagged to the attendees.
4. Delegate to :class:`MemoryInformedWriter.run` with template
   ``meeting_note.md.j2``, ``task_type=draft_meeting_note``, and the
   skill-local ``autonomy_level`` from ``config/memory.yaml``.

Idempotency is handled inside :class:`MemoryInformedWriter` via the
frontmatter ``idempotency_key`` (the calendar event id).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from donna.capabilities._slugify import slugify
from donna.config import MeetingNoteSkillConfig
from donna.integrations.vault import VaultClient
from donna.memory.linking import resolve_person_link
from donna.memory.store import MemoryStore, RetrievedChunk
from donna.memory.writer import MemoryInformedWriter, WriteResult

logger = structlog.get_logger()


@dataclass(frozen=True)
class CalendarEventRow:
    """Subset of ``calendar_mirror`` consumed by the skill + poller.

    Mirrors the columns the poller selects — decoupled from the ORM so
    the skill can be exercised in tests without a SQLAlchemy session.
    """

    event_id: str
    user_id: str
    calendar_id: str
    summary: str
    start_time: datetime
    end_time: datetime
    attendees: str | None  # JSON-encoded list[{name,email}] or None


def _parse_attendees(raw: str | None) -> list[dict[str, str]]:
    """Parse the JSON-encoded ``attendees`` column.

    Returns ``[]`` when the column is NULL or contains malformed JSON —
    a bad blob is not worth failing a meeting-note write over.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("meeting_note_bad_attendees_json", raw=raw)
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        email = str(item.get("email") or "").strip()
        if not name and email:
            name = email.split("@", 1)[0]
        if name:
            out.append({"name": name, "email": email})
    return out


class MeetingNoteSkill:
    """Compose memory-informed context and delegate to the shared writer."""

    TEMPLATE = "meeting_note.md.j2"
    TASK_TYPE = "draft_meeting_note"

    def __init__(
        self,
        *,
        writer: MemoryInformedWriter,
        memory_store: MemoryStore,
        vault_client: VaultClient,
        config: MeetingNoteSkillConfig,
        user_id: str,
    ) -> None:
        self._writer = writer
        self._memory_store = memory_store
        self._vault_client = vault_client
        self._config = config
        self._user_id = user_id

    async def run_for_event(self, event: CalendarEventRow) -> WriteResult:
        """Gather context, resolve people links, and delegate to the writer."""
        target_path = (
            f"Meetings/{event.start_time:%Y-%m-%d}-{slugify(event.summary)}.md"
        )

        async def context_gather() -> dict[str, Any]:
            return await self._gather_context(event)

        return await self._writer.run(
            template=self.TEMPLATE,
            task_type=self.TASK_TYPE,
            context_gather=context_gather,
            target_path=target_path,
            idempotency_key=event.event_id,
            user_id=self._user_id,
            autonomy_level=self._config.autonomy_level,
        )

    async def _gather_context(
        self, event: CalendarEventRow
    ) -> dict[str, Any]:
        attendees = _parse_attendees(event.attendees)

        # Resolve wikilinks concurrently — cheap stat() calls.
        link_tasks = [
            resolve_person_link(a["name"], self._vault_client)
            for a in attendees
        ]
        attendee_links = (
            list(await asyncio.gather(*link_tasks)) if link_tasks else []
        )

        attendee_query = " ".join(a["name"] for a in attendees) or event.summary
        limits = self._config.context_limits

        prior_raw, recent_chats, open_tasks = await asyncio.gather(
            self._memory_store.search(
                query=event.summary,
                user_id=self._user_id,
                k=limits.prior_meetings * 3,  # pre-filter headroom
                sources=["vault"],
            ),
            self._memory_store.search(
                query=attendee_query,
                user_id=self._user_id,
                k=limits.recent_chats,
                sources=["chat"],
            ),
            self._memory_store.search(
                query=attendee_query,
                user_id=self._user_id,
                k=limits.open_tasks,
                sources=["task"],
            ),
        )

        prior_meetings = _filter_meetings(prior_raw)[: limits.prior_meetings]

        return {
            "event": {
                "event_id": event.event_id,
                "summary": event.summary,
                "start_time": event.start_time.isoformat(),
                "end_time": event.end_time.isoformat(),
                "description": "",  # CalendarMirror does not store description today
                "attendees": attendees,
            },
            "attendee_links": attendee_links,
            "prior_meetings": [_hit_to_dict(h) for h in prior_meetings],
            "recent_chats": [_hit_to_dict(h) for h in recent_chats],
            "open_tasks": [_hit_to_dict(h) for h in open_tasks],
        }


def _filter_meetings(hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Post-filter vault hits down to those from meeting notes.

    ``MemoryStore.search`` filters today support only ``path_prefix``;
    filtering by frontmatter ``type`` in Python is simpler than extending
    the filter DSL for this slice. Revisit in Slice 16.
    """
    kept: list[RetrievedChunk] = []
    for hit in hits:
        if hit.metadata.get("type") == "meeting":
            kept.append(hit)
    return kept


def _hit_to_dict(hit: RetrievedChunk) -> dict[str, Any]:
    """Jinja-friendly projection of a search hit."""
    return {
        "title": hit.title or "",
        "source_path": hit.source_path,
        "content": hit.content,
        "score": hit.score,
    }
