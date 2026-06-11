"""Tests for the personal-context provider."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from donna.orchestrator.task_context import build_personal_context


@dataclass
class FakeChunk:
    title: str | None
    content: str


class FakeMemoryStore:
    def __init__(self, hits: list[FakeChunk]) -> None:
        self._hits = hits
        self.search = AsyncMock(return_value=hits)


class FakeApplier:
    def __init__(self, rules: list[dict]) -> None:
        self._rules = rules
        self.load_rules = AsyncMock(return_value=rules)


async def test_empty_when_no_sources() -> None:
    result = await build_personal_context(
        "send an email", "nick", preference_applier=None, memory_store=None,
    )
    assert result == ""


async def test_includes_vault_titles_and_pref_hints() -> None:
    store = FakeMemoryStore([FakeChunk(title="Alice Smith", content="Coworker on Project X.")])
    applier = FakeApplier([
        {"condition": {"keywords": ["dentist"]}, "action": {"field": "domain", "value": "personal"}},
    ])
    result = await build_personal_context(
        "email Alice about Project X", "nick",
        preference_applier=applier, memory_store=store,
    )
    assert "Alice Smith" in result
    assert "Coworker on Project X" in result
    assert "dentist" in result
    assert "personal" in result
    store.search.assert_awaited_once()


async def test_survives_memory_store_error() -> None:
    store = FakeMemoryStore([])
    store.search = AsyncMock(side_effect=RuntimeError("vec0 down"))
    result = await build_personal_context(
        "email Alice", "nick", preference_applier=None, memory_store=store,
    )
    assert result == ""
