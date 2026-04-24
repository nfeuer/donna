"""Unit tests for :class:`donna.memory.chunking.ChatTurnChunker`."""
from __future__ import annotations

from donna.memory.chunking import ChatTurnChunker


def _msg(i: int, role: str, content: str) -> dict[str, object]:
    return {"id": f"m{i}", "role": role, "content": content}


def test_role_flip_flushes_buffer() -> None:
    chunker = ChatTurnChunker(max_tokens=256, min_chars=1)
    msgs = [
        _msg(1, "user", "Hi there, let's plan the week."),
        _msg(2, "user", "Any blockers on the onboarding doc?"),
        _msg(3, "assistant", "No blockers. I have a draft ready."),
        _msg(4, "user", "Great — ship it."),
    ]
    turns = chunker.chunk_messages(msgs)
    assert [t.role for t in turns] == ["user", "assistant", "user"]
    assert turns[0].first_msg_id == "m1"
    assert turns[0].last_msg_id == "m2"
    assert turns[0].message_ids == ["m1", "m2"]
    assert turns[1].first_msg_id == "m3"
    assert turns[2].first_msg_id == "m4"


def test_merge_consecutive_same_role() -> None:
    chunker = ChatTurnChunker(max_tokens=256, min_chars=1)
    msgs = [
        _msg(1, "user", "First line"),
        _msg(2, "user", "Second line"),
        _msg(3, "user", "Third line"),
    ]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) == 1
    assert "First line" in turns[0].content
    assert "Second line" in turns[0].content
    assert "Third line" in turns[0].content


def test_min_chars_drops_short_noise() -> None:
    chunker = ChatTurnChunker(max_tokens=256, min_chars=12, task_verbs=[])
    msgs = [
        _msg(1, "user", "hi"),  # dropped
        _msg(2, "user", "ok cool"),  # dropped (no ? no task verb)
        _msg(3, "user", "longer message is kept"),
    ]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) == 1
    assert turns[0].content == "longer message is kept"
    assert turns[0].first_msg_id == "m3"


def test_task_verb_and_question_rescue_short_messages() -> None:
    chunker = ChatTurnChunker(
        max_tokens=256, min_chars=50, task_verbs=["call", "email"],
    )
    msgs = [
        _msg(1, "user", "hmm"),  # dropped
        _msg(2, "user", "do it?"),  # rescued by `?`
        _msg(3, "user", "call mom"),  # rescued by task verb
    ]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) == 1
    assert "do it?" in turns[0].content
    assert "call mom" in turns[0].content


def test_split_when_buffer_exceeds_max_tokens() -> None:
    # Use a tiny token budget to force a flush mid-buffer.
    chunker = ChatTurnChunker(max_tokens=8, min_chars=1)
    msgs = [
        _msg(1, "user", "alpha beta gamma delta"),
        _msg(2, "user", "epsilon zeta eta theta"),
        _msg(3, "user", "iota kappa"),
    ]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) >= 2
    assert all(t.role == "user" for t in turns)


def test_system_role_forces_boundary() -> None:
    chunker = ChatTurnChunker(
        max_tokens=256, min_chars=1, include_roles=["user", "assistant"],
    )
    msgs = [
        _msg(1, "user", "Morning summary?"),
        _msg(2, "system", "internal"),
        _msg(3, "user", "Any new tasks for me?"),
    ]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) == 2
    assert turns[0].first_msg_id == "m1"
    assert turns[1].first_msg_id == "m3"


def test_turn_span_metadata_covers_all_messages() -> None:
    chunker = ChatTurnChunker(max_tokens=256, min_chars=1)
    msgs = [_msg(i, "user", f"line {i} with content") for i in range(1, 4)]
    turns = chunker.chunk_messages(msgs)
    assert len(turns) == 1
    assert turns[0].first_msg_id == "m1"
    assert turns[0].last_msg_id == "m3"
    assert turns[0].message_ids == ["m1", "m2", "m3"]
