"""Shared pytest fixtures for Wave 1 E2E scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from tests.e2e.harness import build_wave1_test_runtime, Wave1Runtime


@pytest_asyncio.fixture
async def runtime(tmp_path: Path) -> Wave1Runtime:
    rt = await build_wave1_test_runtime(tmp_path)
    try:
        yield rt
    finally:
        await rt.shutdown()
