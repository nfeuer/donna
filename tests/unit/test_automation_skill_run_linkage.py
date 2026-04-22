"""F-2: bidirectional automation_run.skill_run_id <-> skill_run.automation_run_id linkage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import aiosqlite
import pytest
from alembic.config import Config

from alembic import command


def test_skill_run_result_has_run_id_field():
    from donna.skills.executor import SkillRunResult
    result = SkillRunResult(status="succeeded", run_id="abc")
    assert result.run_id == "abc"


@pytest.mark.asyncio
async def test_executor_populates_result_run_id_and_writes_automation_run_id(tmp_path):
    from donna.skills.executor import SkillExecutor
    from donna.skills.models import SkillRow, SkillVersionRow
    from donna.skills.run_persistence import SkillRunRepository

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        # Prereq rows: capability, skill, skill_version, automation_run.
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, 'cap', '', '{}', 'on_message', 'active', ?, 'seed')",
            (str(uuid.uuid4()), now),
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES ('s1', 'cap', 'v1', 'sandbox', 0, ?, ?)",
            (now, now),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES ('v1', 's1', 1, ?, '{}', '{}', 'test', ?)",
            (
                "steps:\n  - name: parse\n    kind: llm\n    prompt: \"parse\"\n"
                "    output_schema:\n      type: object\n",
                now,
            ),
        )
        await conn.commit()

        class _FakeRouter:
            async def complete(self, prompt, task_type, task_id=None, user_id="system"):
                class _Meta:
                    invocation_id = "x"
                    cost_usd = 0.0
                    latency_ms = 1
                return {"ok": True}, _Meta()

        executor = SkillExecutor(
            model_router=_FakeRouter(),
            run_repository=SkillRunRepository(conn),
        )
        skill = SkillRow(
            id="s1", capability_name="cap",
            current_version_id="v1", state="sandbox",
            requires_human_gate=False, baseline_agreement=None,
            created_at=None, updated_at=None,
        )
        version = SkillVersionRow(
            id="v1", skill_id="s1", version_number=1,
            yaml_backbone="steps:\n  - name: parse\n    kind: llm\n    prompt: \"parse\"\n    output_schema:\n      type: object\n",
            step_content={"parse": "parse"},
            output_schemas={"parse": {"type": "object"}},
            created_by="test", changelog=None, created_at=None,
        )

        automation_run_id = str(uuid.uuid4())
        result = await executor.execute(
            skill=skill, version=version, inputs={}, user_id="test",
            automation_run_id=automation_run_id,
        )

        assert result.run_id is not None, "SkillRunResult.run_id must be populated"

        cursor = await conn.execute(
            "SELECT automation_run_id FROM skill_run WHERE id = ?",
            (result.run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == automation_run_id


@pytest.mark.asyncio
async def test_dispatcher_writes_skill_run_id_into_automation_run(tmp_path, monkeypatch):
    """Dispatcher passes automation_run_id into executor and records result.run_id on automation_run."""
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.config import SkillSystemConfig
    from donna.skills.executor import SkillRunResult

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        # Use a unique capability name to avoid collision with Wave 2's
        # seeded `product_watch` (state=sandbox) — this test needs
        # shadow_primary to exercise the skill path.
        now = datetime.now(UTC).isoformat()
        capability_name = "test_cap_linkage"
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, ?, '', '{}', 'on_schedule', 'active', ?, 'seed')",
            (str(uuid.uuid4()), capability_name, now),
        )
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES (?, ?, ?, 'shadow_primary', 0, ?, ?)",
            (skill_id, capability_name, version_id, now, now),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', ?)",
            (version_id, skill_id, now),
        )
        automation_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO automation (id, user_id, name, description, "
            "capability_name, inputs, trigger_type, schedule, "
            "alert_conditions, alert_channels, max_cost_per_run_usd, "
            "min_interval_seconds, status, last_run_at, next_run_at, "
            "run_count, failure_count, created_at, updated_at, created_via) "
            "VALUES (?, 'nick', 'watch', NULL, ?, '{}', "
            "'on_schedule', '0 * * * *', '{}', '[]', 1.0, 300, 'active', NULL, ?, "
            "0, 0, ?, ?, 'dashboard')",
            (automation_id, capability_name, now, now, now),
        )
        await conn.commit()

        captured_calls = []
        synthetic_skill_run_id = str(uuid.uuid4())

        class _FakeExecutor:
            async def execute(self, **kwargs):
                captured_calls.append(kwargs)
                return SkillRunResult(
                    status="succeeded",
                    final_output={"ok": True},
                    run_id=synthetic_skill_run_id,
                )

        dispatcher = AutomationDispatcher(
            connection=conn,
            repository=AutomationRepository(conn),
            model_router=MagicMock(),
            skill_executor_factory=lambda: _FakeExecutor(),
            budget_guard=None,
            alert_evaluator=MagicMock(evaluate=lambda output, conditions: False),
            cron=MagicMock(next_run=lambda schedule, from_time: from_time),
            notifier=None,
            config=SkillSystemConfig(),
        )

        cursor = await conn.execute(
            "SELECT * FROM automation WHERE id = ?", (automation_id,),
        )
        # Fetch via repository.
        auto = await AutomationRepository(conn).get(automation_id)
        await dispatcher.dispatch(auto)

        # 1. Executor received automation_run_id.
        assert len(captured_calls) == 1
        assert "automation_run_id" in captured_calls[0]
        assert captured_calls[0]["automation_run_id"] is not None

        # 2. automation_run row has skill_run_id set to the synthetic id.
        cursor = await conn.execute(
            "SELECT skill_run_id FROM automation_run WHERE automation_id = ?",
            (automation_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == synthetic_skill_run_id
