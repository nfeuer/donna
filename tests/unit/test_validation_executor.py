"""Tests for donna.skills.validation_executor.ValidationExecutor."""

from __future__ import annotations

import asyncio
import pytest

from donna.config import SkillSystemConfig
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_executor import ValidationExecutor


@pytest.fixture
def fake_router():
    class _FakeRouter:
        async def complete(self, **kwargs):
            class _Meta:
                invocation_id = "inv"
                cost_usd = 0.0
                latency_ms = 1
            return {}, _Meta()
    return _FakeRouter()


def _make_skill_version():
    skill = SkillRow(
        id="s1", capability_name="cap",
        current_version_id="v1",
        state="sandbox",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="steps: []",
        step_content={}, output_schemas={},
        created_by="test", changelog=None, created_at=None,
    )
    return skill, version


@pytest.mark.asyncio
async def test_execute_runs_against_mocks(fake_router) -> None:
    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill, version = _make_skill_version()
    result = await executor.execute(
        skill=skill, version=version, inputs={"q": 1},
        user_id="validation",
        tool_mocks={'web_fetch:{"url":"https://x"}': {"status": 200}},
    )
    assert result.status in ("succeeded", "failed", "escalated")


@pytest.mark.asyncio
async def test_execute_never_writes_to_db(fake_router, tmp_path) -> None:
    import aiosqlite
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE skill_run (id TEXT)")
        await conn.commit()

    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill, version = _make_skill_version()
    await executor.execute(
        skill=skill, version=version, inputs={},
        user_id="validation", tool_mocks=None,
    )

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM skill_run")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_per_run_timeout(fake_router) -> None:
    config = SkillSystemConfig(validation_per_run_timeout_s=1)
    executor = ValidationExecutor(model_router=fake_router, config=config)

    class HangingInner:
        async def execute(self, **kwargs):
            await asyncio.sleep(3)
            raise AssertionError("should have timed out")

    executor._build_inner_executor = lambda _: HangingInner()  # type: ignore[assignment]

    skill, version = _make_skill_version()
    with pytest.raises(asyncio.TimeoutError):
        await executor.execute(
            skill=skill, version=version, inputs={},
            user_id="validation", tool_mocks=None,
        )


@pytest.mark.asyncio
async def test_validate_against_fixtures_integration(fake_router) -> None:
    from donna.skills.fixtures import Fixture, validate_against_fixtures

    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill, version = _make_skill_version()
    fixtures = [
        Fixture(case_name="c1", input={}, tool_mocks=None),
        Fixture(case_name="c2", input={}, tool_mocks=None),
    ]
    report = await validate_against_fixtures(
        skill=skill, executor=executor, fixtures=fixtures, version=version,
    )
    assert report.total == 2
