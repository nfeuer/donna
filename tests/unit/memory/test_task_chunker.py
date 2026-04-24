"""Unit tests for :class:`donna.memory.chunking.TaskChunker`."""
from __future__ import annotations

from donna.memory.chunking import TaskChunker


def test_single_chunk_for_typical_task() -> None:
    chunker = TaskChunker(max_tokens=256)
    chunks = chunker.chunk_task(
        {
            "id": "t1",
            "title": "Call Sarah",
            "description": "Follow up on onboarding.",
            "status": "backlog",
            "domain": "work",
            "notes": ["ping on Slack", "schedule 30 min"],
        }
    )
    assert len(chunks) == 1
    assert "Call Sarah" in chunks[0].content
    assert "ping on Slack" in chunks[0].content


def test_notes_json_string_parses_to_list() -> None:
    chunker = TaskChunker(max_tokens=256)
    chunks = chunker.chunk_task(
        {
            "id": "t1",
            "title": "Task",
            "notes": '["one", "two", "three"]',
        }
    )
    assert len(chunks) == 1
    content = chunks[0].content
    assert "- one" in content and "- two" in content and "- three" in content


def test_splits_at_notes_boundary_when_long() -> None:
    # Small cap forces at least one head chunk + >=1 notes chunks.
    chunker = TaskChunker(max_tokens=16)
    chunks = chunker.chunk_task(
        {
            "id": "t1",
            "title": "Long task with many notes",
            "description": "A fairly long description that crowds the header chunk.",
            "status": "in_progress",
            "notes": [f"note number {i} with some descriptive content" for i in range(12)],
        }
    )
    assert len(chunks) >= 2
    # Head chunk is the title+description; subsequent chunks start
    # with the "Notes:" marker.
    assert "Long task" in chunks[0].content
    assert any("Notes:" in c.content for c in chunks[1:])


def test_empty_notes_is_header_only() -> None:
    chunker = TaskChunker(max_tokens=256)
    chunks = chunker.chunk_task(
        {"id": "t1", "title": "Just a title", "notes": None}
    )
    assert len(chunks) == 1
    assert "Just a title" in chunks[0].content
    assert "Notes:" not in chunks[0].content
