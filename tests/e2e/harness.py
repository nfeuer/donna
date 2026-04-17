"""E2E harness — build a minimal Wave 1 orchestrator runtime for testing.

Mirrors the production wiring in src/donna/cli.py:_run_orchestrator but
with fakes for Ollama, Claude, and the Discord bot so tests run in
seconds on CI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config


@dataclass
class _Invocation:
    task_type: str
    prompt: str | None
    output: dict


class FakeOllama:
    def __init__(self, canned: dict[str, dict] | None = None) -> None:
        self.canned = canned or {}
        self.invocations: list[_Invocation] = []

    async def complete(self, *, task_type: str, prompt: str | None = None, **_kw) -> tuple[dict, Any]:
        output = dict(self.canned.get(task_type, {"_default": True}))
        self.invocations.append(_Invocation(task_type=task_type, prompt=prompt, output=output))

        class _Meta:
            invocation_id = f"fake-{len(self.invocations)}"
            cost_usd = 0.0
            latency_ms = 1

        return output, _Meta()


class FakeClaude:
    def __init__(self, canned: dict[str, dict] | None = None) -> None:
        self.canned = canned or {}
        self.invocations: list[_Invocation] = []

    async def complete(self, *, task_type: str, prompt: str | None = None, **_kw) -> tuple[dict, Any]:
        output = dict(self.canned.get(task_type, {"_default": True}))
        self.invocations.append(_Invocation(task_type=task_type, prompt=prompt, output=output))

        class _Meta:
            invocation_id = f"fake-claude-{len(self.invocations)}"
            cost_usd = 0.01
            latency_ms = 10

        return output, _Meta()


class FakeRouter:
    """Route based on task_type to Ollama fake or Claude fake."""

    def __init__(self, ollama: FakeOllama, claude: FakeClaude) -> None:
        self._ollama = ollama
        self._claude = claude

    async def complete(self, *, task_type: str, **kw) -> tuple[dict, Any]:
        if task_type.startswith("skill_validation::") or task_type.startswith("chat_"):
            return await self._ollama.complete(task_type=task_type, **kw)
        return await self._claude.complete(task_type=task_type, **kw)


class FakeDonnaBot:
    """FakeDonnaBot satisfies BotProtocol; records every send."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, str, str]] = []

    async def send_message(self, channel: str, content: str) -> None:
        self.sends.append(("channel", channel, content))

    async def send_embed(self, channel: str, embed: Any) -> None:
        self.sends.append(("embed", channel, str(embed)))

    async def send_to_thread(self, thread_id: int, content: str) -> None:
        self.sends.append(("thread", str(thread_id), content))


@dataclass
class Wave1Runtime:
    db: Any
    fake_ollama: FakeOllama
    fake_claude: FakeClaude
    fake_router: FakeRouter
    fake_bot: FakeDonnaBot
    notification_service: Any
    skill_bundle: Any
    skill_config: Any
    automation_dispatcher: Any
    automation_scheduler: Any
    automation_repo: Any
    cost_tracker: Any

    async def shutdown(self) -> None:
        await self.db.close()


async def build_wave1_test_runtime(tmp_path: Path, **overrides) -> Wave1Runtime:
    """Build a fully-wired Wave 1 runtime backed by a throwaway SQLite DB."""
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine
    from donna.config import (
        load_calendar_config, load_state_machine_config, SkillSystemConfig,
    )
    from donna.notifications.service import NotificationService
    from donna.cost.tracker import CostTracker
    from donna.skills.startup_wiring import assemble_skill_system
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.automations.scheduler import AutomationScheduler

    db_path = tmp_path / "e2e.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    config_dir = Path("config")
    sm_config = load_state_machine_config(config_dir)
    state_machine = StateMachine(sm_config)
    db = Database(db_path, state_machine)
    await db.connect()

    fake_ollama = FakeOllama(overrides.get("ollama_canned"))
    fake_claude = FakeClaude(overrides.get("claude_canned"))
    fake_router = FakeRouter(fake_ollama, fake_claude)
    fake_bot = FakeDonnaBot()

    calendar_cfg = load_calendar_config(config_dir)
    if hasattr(calendar_cfg, "time_windows"):
        calendar_cfg.time_windows.blackout.start_hour = 0
        calendar_cfg.time_windows.blackout.end_hour = 0
        calendar_cfg.time_windows.quiet_hours.start_hour = 0
        calendar_cfg.time_windows.quiet_hours.end_hour = 0
    notification_service = NotificationService(
        bot=fake_bot,
        calendar_config=calendar_cfg,
        user_id="test-user",
    )

    skill_config = SkillSystemConfig(enabled=True, nightly_run_hour_utc=3)
    cost_tracker = CostTracker(db.connection)

    class _FakeBudget:
        async def check_pre_call(self, **kw):
            return None

    bundle = assemble_skill_system(
        connection=db.connection,
        model_router=fake_router,
        budget_guard=_FakeBudget(),
        notifier=lambda m: asyncio.sleep(0),
        config=skill_config,
        validation_executor_factory=None,
    )

    automation_repo = AutomationRepository(db.connection)
    automation_dispatcher = AutomationDispatcher(
        connection=db.connection,
        repository=automation_repo,
        model_router=fake_router,
        skill_executor_factory=lambda: None,
        budget_guard=_FakeBudget(),
        alert_evaluator=AlertEvaluator(),
        cron=CronScheduleCalculator(),
        notifier=notification_service,
        config=skill_config,
    )
    automation_scheduler = AutomationScheduler(
        repository=automation_repo,
        dispatcher=automation_dispatcher,
        poll_interval_seconds=1,
    )

    return Wave1Runtime(
        db=db,
        fake_ollama=fake_ollama,
        fake_claude=fake_claude,
        fake_router=fake_router,
        fake_bot=fake_bot,
        notification_service=notification_service,
        skill_bundle=bundle,
        skill_config=skill_config,
        automation_dispatcher=automation_dispatcher,
        automation_scheduler=automation_scheduler,
        automation_repo=automation_repo,
        cost_tracker=cost_tracker,
    )
