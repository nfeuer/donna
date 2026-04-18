"""PendingDraftRegistry — per-user pending task/automation drafts."""
from __future__ import annotations

import time

import pytest

from donna.integrations.discord_pending_drafts import (
    PendingDraft,
    PendingDraftRegistry,
)


def test_set_and_get_by_thread() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="automation", partial={"url": "x"})
    reg.set(draft)
    assert reg.get_by_thread(42) == draft


def test_ttl_expires_draft() -> None:
    reg = PendingDraftRegistry(ttl_seconds=0)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={})
    reg.set(draft)
    draft.created_at = time.time() - 3600
    assert reg.get_by_thread(42) is None


def test_list_active_for_user() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    reg.set(PendingDraft(user_id="u1", thread_id=1, draft_kind="task", partial={}))
    reg.set(PendingDraft(user_id="u2", thread_id=2, draft_kind="automation", partial={}))
    assert len(reg.list_active_for_user("u1")) == 1


def test_discard() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    reg.set(PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={}))
    reg.discard(42)
    assert reg.get_by_thread(42) is None


@pytest.mark.asyncio
async def test_sweeper_removes_expired() -> None:
    reg = PendingDraftRegistry(ttl_seconds=0)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={})
    draft.created_at = time.time() - 3600
    reg._drafts[42] = draft  # direct insert to bypass set's timestamp
    removed = await reg.sweep_expired()
    assert removed == 1
    assert reg.get_by_thread(42) is None


@pytest.mark.asyncio
async def test_sweeper_preserves_refreshed_draft() -> None:
    """F-W3-B: a draft refreshed between list-build and pop should survive.

    The original sweep_expired implementation captured a snapshot list
    of expired thread_ids and popped them unconditionally. If set()
    refreshed the draft between the snapshot and the pop, the fresh
    entry got evicted. The fix re-checks TTL at pop time.
    """
    reg = PendingDraftRegistry(ttl_seconds=1)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={})
    reg._drafts[42] = draft
    draft.created_at = time.time() - 3600  # initially expired
    # Simulate a refresh happening between "list keys" and "pop".
    draft.created_at = time.time()  # refreshed
    removed = await reg.sweep_expired()
    assert removed == 0
    assert reg.get_by_thread(42) is not None
