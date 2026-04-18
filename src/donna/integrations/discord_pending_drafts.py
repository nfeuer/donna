"""PendingDraftRegistry — per-user in-memory map of task/automation drafts.

Thread-id keyed. 30-min TTL. Lost on process restart (acceptable for v1).
Promoted from the Wave 1/2 task-clarification primitive in discord_bot.py;
extended to hold automation partial drafts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingDraft:
    user_id: str
    thread_id: int | str
    draft_kind: str  # task | automation
    partial: dict[str, Any]
    capability_name: str | None = None
    created_at: float = field(default_factory=time.time)


class PendingDraftRegistry:
    def __init__(self, *, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._drafts: dict[int | str, PendingDraft] = {}

    def set(self, draft: PendingDraft) -> None:
        draft.created_at = time.time()
        self._drafts[draft.thread_id] = draft

    def get_by_thread(self, thread_id: int | str) -> PendingDraft | None:
        draft = self._drafts.get(thread_id)
        if draft is None:
            return None
        if time.time() - draft.created_at > self._ttl:
            self._drafts.pop(thread_id, None)
            return None
        return draft

    def list_active_for_user(self, user_id: str) -> list[PendingDraft]:
        now = time.time()
        return [
            d
            for d in self._drafts.values()
            if d.user_id == user_id and now - d.created_at <= self._ttl
        ]

    def discard(self, thread_id: int | str) -> None:
        self._drafts.pop(thread_id, None)

    async def sweep_expired(self) -> int:
        """Remove drafts older than TTL.

        F-W3-B: the previous implementation built a list of expired
        thread_ids up front, then popped them. If ``set()`` refreshed a
        draft (resetting ``created_at``) between the list build and the
        pop, the refreshed draft was still evicted. Re-checking the TTL
        at pop time closes that window.
        """
        now = time.time()
        removed = 0
        for tid in list(self._drafts.keys()):
            draft = self._drafts.get(tid)
            if draft is None:
                continue
            if now - draft.created_at > self._ttl:
                # Re-read inside the loop to avoid racing with set().
                current = self._drafts.get(tid)
                if current is not None and now - current.created_at > self._ttl:
                    self._drafts.pop(tid, None)
                    removed += 1
        return removed
