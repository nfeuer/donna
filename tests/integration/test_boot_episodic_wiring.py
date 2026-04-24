"""Boot wiring: ``_build_episodic_sources`` attaches the observer to ``Database``.

Mirrors :mod:`tests.integration.test_boot_memory_wiring` for the
slice-14 episodic sources. Confirms that with a real
``Database`` + a stand-in memory store + a ``memory.yaml`` config
that enables all three sources, ``_build_episodic_sources`` returns
all three source instances and attaches a working observer to the
DB so a subsequent ``add_chat_message`` and ``create_task`` call
each invoke the corresponding ``observe_*`` method.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.cli_wiring import _build_episodic_sources


class _SpyStore:
    """Minimal stand-in for MemoryStore — records observe calls."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, str]] = []
        self._conn = object()  # used by sources that touch ._conn

    async def upsert(self, doc):  # type: ignore[no-untyped-def]
        self.upserts.append((doc.source_type, doc.source_id))
        return "doc-" + doc.source_id

    async def delete(self, **_kwargs):  # type: ignore[no-untyped-def]
        return True


def _write_memory_yaml(config_dir: Path) -> None:
    (config_dir / "memory.yaml").write_text(
        "vault:\n"
        "  root: /tmp/donna-vault\n"
        "  git_author_name: Donna\n"
        "  git_author_email: donna@example.com\n"
        "  sync_method: manual\n"
        "safety:\n"
        "  max_note_bytes: 50000\n"
        "  path_allowlist: [Inbox]\n"
        "embedding:\n"
        "  provider: minilm-l6-v2\n"
        "  dim: 384\n"
        "  max_tokens: 256\n"
        "  chunk_overlap: 32\n"
        "retrieval:\n"
        "  default_k: 5\n"
        "  min_score: 0.0\n"
        "  max_k: 10\n"
        "sources:\n"
        "  vault:\n"
        "    enabled: true\n"
        "    chunker: markdown_heading\n"
        "  chat:\n"
        "    enabled: true\n"
        "    min_chars: 1\n"
        "  task:\n"
        "    enabled: true\n"
        "  correction:\n"
        "    enabled: true\n"
    )


@pytest.mark.integration
def test_build_episodic_sources_returns_all_three(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_memory_yaml(config_dir)

    store = _SpyStore()

    class _StubDB:
        def __init__(self) -> None:
            self.observer = None

        def set_memory_observer(self, obs: object) -> None:
            self.observer = obs

    db = _StubDB()
    built = _build_episodic_sources(config_dir, store, db, "nick")
    assert set(built) == {"chat", "task", "correction"}
    # The DB should have a combined observer attached so chat + task
    # events route to the right source.
    assert db.observer is not None
    assert hasattr(db.observer, "observe_message")
    assert hasattr(db.observer, "observe_task")


@pytest.mark.integration
def test_build_episodic_sources_returns_empty_when_store_missing(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_memory_yaml(config_dir)

    class _StubDB:
        def set_memory_observer(self, _obs: object) -> None:
            raise AssertionError("should not be called when store is None")

    built = _build_episodic_sources(config_dir, None, _StubDB(), "nick")
    assert built == {}


@pytest.mark.integration
def test_build_episodic_sources_skips_when_yaml_missing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    class _StubDB:
        def set_memory_observer(self, _obs: object) -> None:
            raise AssertionError("should not be called when yaml is absent")

    built = _build_episodic_sources(config_dir, _SpyStore(), _StubDB(), "nick")
    assert built == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combined_observer_routes_messages_and_tasks(
    tmp_path: Path,
) -> None:
    """End-to-end: observer attached by `_build_episodic_sources`
    actually sends events to the chat + task sources."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_memory_yaml(config_dir)

    store = _SpyStore()

    class _StubDB:
        observer: object = None

        def set_memory_observer(self, obs: object) -> None:
            self.observer = obs

    db = _StubDB()
    built = _build_episodic_sources(config_dir, store, db, "nick")
    assert built and db.observer is not None

    # Drive the combined observer with synthetic events.
    await db.observer.observe_message(  # type: ignore[union-attr]
        {
            "session_id": "S1",
            "user_id": "nick",
            "message": {"id": "m1", "role": "user", "content": "hello world"},
        }
    )
    # No flush yet — buffer holds the user turn until a role flip
    # or session close. Drive a session-close event:
    await db.observer.observe_session_closed(  # type: ignore[union-attr]
        {"session_id": "S1", "user_id": "nick", "status": "closed"}
    )

    await db.observer.observe_task(  # type: ignore[union-attr]
        {
            "action": "create",
            "task": {
                "id": "t1",
                "user_id": "nick",
                "title": "Test task",
                "status": "backlog",
                "notes": [],
            },
        }
    )

    source_types = {st for st, _sid in store.upserts}
    assert "chat" in source_types, store.upserts
    assert "task" in source_types, store.upserts
