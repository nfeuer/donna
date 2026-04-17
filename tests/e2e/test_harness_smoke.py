"""Smoke test: the E2E harness builds a runtime with all wave-1 components."""

from __future__ import annotations

import pytest

from tests.e2e.harness import build_wave1_test_runtime


@pytest.mark.asyncio
async def test_harness_constructs_all_components(tmp_path) -> None:
    rt = await build_wave1_test_runtime(tmp_path)
    try:
        assert rt.db is not None
        assert rt.notification_service is not None
        assert rt.fake_bot is not None
        assert rt.fake_router is not None
        assert rt.skill_bundle is not None
        assert rt.automation_scheduler is not None
        assert rt.automation_dispatcher is not None
    finally:
        await rt.shutdown()
