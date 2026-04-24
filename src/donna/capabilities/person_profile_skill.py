"""Slice 16 — weekly ``People/{name}.md`` profile fill skill.

Two triggers fan into one skill entry point:

- **mention_threshold** — :class:`PersonMentionCounter` sweeps the last
  ``lookback_days`` of ``memory_chunks`` and returns names that exceed
  ``trigger_mentions_threshold``.
- **stub_fill** — a weekly scan of ``People/*.md`` lists every note
  whose body is shorter than ``min_body_chars`` (i.e. stubs created by
  :func:`donna.memory.person_stub.ensure_person_stubs`).

Both produce ``run_for_person(name, reason)`` calls. The skill reads
the existing note before delegating to :class:`MemoryInformedWriter`
so it can enforce an overwrite guard — only empty notes or
Donna-owned autowrites are (re)rendered.

Idempotency: ``{name}@{iso_week}`` — re-renders weekly as new context
accrues but is a no-op within a week.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal

import structlog

from donna.capabilities.daily_reflection_skill import _hit_to_dict
from donna.capabilities.person_mention_counter import (
    PersonMentionCounter,
    names_above_threshold,
)
from donna.config import PersonProfileSkillConfig
from donna.integrations.vault import VaultClient, VaultReadError
from donna.memory.store import MemoryStore
from donna.memory.writer import MemoryInformedWriter, WriteResult

logger = structlog.get_logger()

PersonProfileReason = Literal["mention_threshold", "stub_fill"]


class PersonProfileSkill:
    TEMPLATE = "person_profile.md.j2"
    TASK_TYPE = "draft_person_profile"

    def __init__(
        self,
        *,
        writer: MemoryInformedWriter,
        memory_store: MemoryStore,
        vault_client: VaultClient,
        mention_counter: PersonMentionCounter,
        config: PersonProfileSkillConfig,
        user_id: str,
    ) -> None:
        self._writer = writer
        self._memory_store = memory_store
        self._vault_client = vault_client
        self._mention_counter = mention_counter
        self._config = config
        self._user_id = user_id

    async def list_names_to_refresh(
        self, *, today: date | None = None
    ) -> list[tuple[str, PersonProfileReason]]:
        """Fan-in both triggers; return a de-duplicated ``(name, reason)`` list."""
        today = today or date.today()
        mention_reason: list[tuple[str, PersonProfileReason]] = []
        counts = await self._mention_counter.scan(
            user_id=self._user_id,
            lookback_days=self._config.lookback_days,
        )
        for name, _ in names_above_threshold(
            counts, self._config.trigger_mentions_threshold
        ):
            mention_reason.append((name, "mention_threshold"))

        stub_reason: list[tuple[str, PersonProfileReason]] = []
        people_paths = await self._list_people_notes()
        for rel in people_paths:
            # ``People/Alice.md`` → ``Alice``.
            name = rel.rsplit("/", 1)[-1].removesuffix(".md")
            try:
                note = await self._vault_client.read(rel)
            except VaultReadError:
                continue
            if len(note.content.strip()) < self._config.min_body_chars:
                stub_reason.append((name, "stub_fill"))

        seen: dict[str, PersonProfileReason] = {}
        for name, reason in mention_reason + stub_reason:
            seen.setdefault(name, reason)
        return list(seen.items())

    async def _list_people_notes(self) -> list[str]:
        try:
            rels = await self._vault_client.list("People", recursive=True)
        except VaultReadError:
            return []
        return [r for r in rels if r.endswith(".md")]

    async def run_for_person(
        self, name: str, reason: PersonProfileReason, *, today: date | None = None
    ) -> WriteResult:
        today = today or date.today()
        iso_year, iso_week, _ = today.isocalendar()
        iso_week_label = f"{iso_year}-W{iso_week:02d}"

        target_path = f"People/{name}.md"
        if not await self._is_writable(target_path):
            logger.info(
                "person_profile_skipped_user_owned",
                name=name,
                reason=reason,
                path=target_path,
            )
            return WriteResult(
                path=target_path,
                sha=None,
                skipped=True,
                reason="user_owned",
            )

        async def context_gather() -> dict[str, Any]:
            return await self._gather_context(name, reason, iso_week_label)

        logger.info(
            "person_profile_triggered",
            name=name,
            reason=reason,
            iso_week=iso_week_label,
        )
        return await self._writer.run(
            template=self.TEMPLATE,
            task_type=self.TASK_TYPE,
            context_gather=context_gather,
            target_path=target_path,
            idempotency_key=f"{name}@{iso_week_label}",
            user_id=self._user_id,
            autonomy_level=self._config.autonomy_level,
        )

    async def _is_writable(self, path: str) -> bool:
        """Return True if the note is empty, missing, or Donna-owned."""
        try:
            note = await self._vault_client.read(path)
        except VaultReadError:
            return True  # missing → safe to create
        if not note.content.strip():
            return True
        return note.frontmatter.get("autowritten_by") == "donna"

    async def _gather_context(
        self,
        name: str,
        reason: PersonProfileReason,
        iso_week_label: str,
    ) -> dict[str, Any]:
        limits = self._config.context_limits
        query = name

        vault_hits, chat_hits, task_hits, correction_hits = await asyncio.gather(
            self._memory_store.search(
                query=query,
                user_id=self._user_id,
                k=limits.vault_hits,
                sources=["vault"],
            ),
            self._memory_store.search(
                query=query,
                user_id=self._user_id,
                k=limits.chat_hits,
                sources=["chat"],
            ),
            self._memory_store.search(
                query=query,
                user_id=self._user_id,
                k=limits.task_hits,
                sources=["task"],
            ),
            self._memory_store.search(
                query=query,
                user_id=self._user_id,
                k=limits.correction_hits,
                sources=["correction"],
            ),
        )

        existing_body = ""
        try:
            note = await self._vault_client.read(f"People/{name}.md")
            existing_body = note.content
        except VaultReadError:
            pass

        return {
            "person": {
                "name": name,
                "trigger_reason": reason,
                "last_refreshed_week": iso_week_label,
            },
            "existing_body": existing_body,
            "vault_hits": [_hit_to_dict(h) for h in vault_hits],
            "chat_hits": [_hit_to_dict(h) for h in chat_hits],
            "task_hits": [_hit_to_dict(h) for h in task_hits],
            "correction_hits": [_hit_to_dict(h) for h in correction_hits],
        }
