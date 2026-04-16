from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.startup_wiring import SkillSystemBundle, assemble_skill_system


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "wiring.db"))
    yield conn
    await conn.close()


async def test_bundle_returns_none_when_disabled(db):
    config = SkillSystemConfig(enabled=False)
    bundle = assemble_skill_system(
        connection=db, model_router=MagicMock(), budget_guard=AsyncMock(),
        notifier=AsyncMock(), config=config,
    )
    assert bundle is None


async def test_bundle_wires_all_components_when_enabled(db):
    config = SkillSystemConfig(enabled=True)
    bundle = assemble_skill_system(
        connection=db, model_router=MagicMock(), budget_guard=AsyncMock(),
        notifier=AsyncMock(), config=config,
    )
    assert bundle is not None
    assert bundle.lifecycle_manager is not None
    assert bundle.shadow_sampler is not None
    assert bundle.auto_drafter is not None
    assert bundle.detector is not None
    assert bundle.degradation is not None
    assert bundle.evolver is not None
    assert bundle.evolution_scheduler is not None
    assert bundle.correction_cluster is not None
    assert bundle.config is config


async def test_bundle_shadow_sampler_has_lifecycle_manager(db):
    config = SkillSystemConfig(enabled=True)
    bundle = assemble_skill_system(
        connection=db, model_router=MagicMock(), budget_guard=AsyncMock(),
        notifier=AsyncMock(), config=config,
    )
    # ShadowSampler's lifecycle_manager reference is the same instance.
    assert bundle.shadow_sampler._lifecycle is bundle.lifecycle_manager
