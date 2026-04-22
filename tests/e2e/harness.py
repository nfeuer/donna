"""E2E harness — build a minimal Wave 1 orchestrator runtime for testing.

Mirrors the production wiring in src/donna/cli.py:_run_orchestrator but
with fakes for Ollama, Claude, and the Discord bot so tests run in
seconds on CI.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from alembic.config import Config

from alembic import command

# ---------------------------------------------------------------------------
# Schema loader — used by FakeRouter.get_output_schema so Wave 3 agents that
# call validate_output() (ClaudeNoveltyJudge, prep_agent, decomposition, …)
# see the real JSON schema on disk without touching the real ModelRouter.
# ---------------------------------------------------------------------------


_SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[2] / "schemas"

# Maps task_type -> schema filename on disk. If a task_type is not in this
# map, FakeRouter.get_output_schema returns an empty schema (permits any
# dict), which mirrors the behaviour MagicMock(return_value={}) gets in the
# unit-test suite for agent tests.
_SCHEMA_FILE_BY_TASK_TYPE: dict[str, str] = {
    "claude_novelty": "claude_novelty.json",
    "challenge_task": "challenger_parse.json",
    "parse_task": "task_parse_output.json",
    "skill_auto_draft": "skill_auto_draft_output.json",
    "skill_equivalence": "skill_equivalence_output.json",
    "skill_evolution": "skill_evolution_output.json",
    "chat_intent": "chat_intent_output.json",
    "chat_respond": "chat_respond_output.json",
    "chat_summarize": "chat_summarize_output.json",
    "prep": "prep_output.json",
    "decompose": "decompose_output.json",
    "dedup": "dedup_output.json",
    "digest": "digest_output.json",
    "extract_preferences": "extract_preferences_output.json",
    "nudge": "nudge_output.json",
    "priority": "priority_output.json",
    "reminder": "reminder_output.json",
    "weekly_digest": "weekly_digest_output.json",
}


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
    """Route based on task_type to Ollama fake or Claude fake.

    Signature matches ``ModelRouter.complete`` exactly — no ``**kwargs`` — so
    drift in caller kwargs (e.g. leftover ``schema=``/``model_alias=``) blows
    up tests instead of being silently swallowed. See F-W1-C.

    Also exposes ``get_output_schema`` so agents that post-validate LLM JSON
    (ClaudeNoveltyJudge, prep_agent, decomposition, rule_extractor, dedup) can
    load real schemas from disk.
    """

    def __init__(self, ollama: FakeOllama, claude: FakeClaude) -> None:
        self._ollama = ollama
        self._claude = claude
        self._schema_cache: dict[str, dict] = {}

    async def complete(
        self,
        prompt: str,
        task_type: str,
        task_id: str | None = None,
        user_id: str = "system",
    ) -> tuple[dict, Any]:
        if task_type.startswith("skill_validation::") or task_type.startswith("chat_"):
            return await self._ollama.complete(task_type=task_type, prompt=prompt)
        return await self._claude.complete(task_type=task_type, prompt=prompt)

    def get_output_schema(self, task_type: str) -> dict:
        """Load the on-disk JSON schema for a task_type, cached.

        Unknown task_types return an empty schema (``{}``) — matches the
        MagicMock(return_value={}) pattern used in the unit-agent suite so
        agents that call ``validate_output(data, schema)`` treat the output
        as unconstrained rather than crashing.
        """
        if task_type in self._schema_cache:
            return self._schema_cache[task_type]
        fname = _SCHEMA_FILE_BY_TASK_TYPE.get(task_type)
        if fname is None:
            schema: dict = {}
        else:
            path = _SCHEMA_DIR / fname
            try:
                schema = json.loads(path.read_text())
            except FileNotFoundError:
                schema = {}
        self._schema_cache[task_type] = schema
        return schema


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
    # Wave 3 additions (intent dispatcher + creation + cadence wiring).
    intent_dispatcher: Any = None
    creation_path: Any = None
    cadence_policy: Any = None
    cadence_reclamper: Any = None

    async def shutdown(self) -> None:
        await self.db.close()


async def build_wave1_test_runtime(tmp_path: Path, **overrides) -> Wave1Runtime:
    """Build a fully-wired Wave 1 runtime backed by a throwaway SQLite DB."""
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.automations.scheduler import AutomationScheduler
    from donna.config import (
        SkillSystemConfig,
        load_calendar_config,
        load_state_machine_config,
    )
    from donna.cost.tracker import CostTracker
    from donna.notifications.service import NotificationService
    from donna.skills.startup_wiring import assemble_skill_system
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine

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

    # ---- Wave 3: intent dispatcher + creation path + cadence wiring -----
    intent_dispatcher, creation_path, cadence_policy, cadence_reclamper = (
        await _build_wave3_intent_pipeline(
            db=db,
            skill_config=skill_config,
            bundle=bundle,
            automation_repo=automation_repo,
            fake_router=fake_router,
            config_dir=config_dir,
        )
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
        intent_dispatcher=intent_dispatcher,
        creation_path=creation_path,
        cadence_policy=cadence_policy,
        cadence_reclamper=cadence_reclamper,
    )


# ---------------------------------------------------------------------------
# Wave 3 intent pipeline assembly (mirrors cli_wiring._build_intent_dispatcher
# but uses the fake router + bundle already built by build_wave1_test_runtime).
# ---------------------------------------------------------------------------


class _SkillLifecycleStateAdapter:
    """Expose ``async current_state(capability_name) -> str`` over skill table."""

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    async def current_state(self, capability_name: str) -> str:
        try:
            cursor = await self._conn.execute(
                "SELECT state FROM skill WHERE capability_name = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (capability_name,),
            )
            row = await cursor.fetchone()
        except Exception:
            return "claude_native"
        if row is None:
            return "claude_native"
        return row[0]


class _TasksDbAdapter:
    """Minimal adapter from Database.create_task to dispatcher.insert_task."""

    def __init__(self, database: Any) -> None:
        self._db = database

    async def insert_task(
        self,
        *,
        user_id: str,
        title: str,
        inputs: dict[str, Any] | None = None,
        deadline: Any | None = None,
        capability_name: str | None = None,
    ) -> str:
        from donna.tasks.db_models import InputChannel

        row = await self._db.create_task(
            user_id=user_id,
            title=title,
            deadline=deadline,
            created_via=InputChannel.DISCORD,
            capability_name=capability_name,
            inputs=inputs,
        )
        return row.id


class _SchedulerComputeNextRun:
    """Thin wrapper exposing ``compute_next_run`` to CadenceReclamper."""

    def __init__(self) -> None:
        from donna.automations.cron import CronScheduleCalculator

        self._cron = CronScheduleCalculator()

    async def compute_next_run(self, cron: str):
        from datetime import datetime

        return self._cron.next_run(
            expression=cron, after=datetime.now(UTC)
        )


async def _build_wave3_intent_pipeline(
    *,
    db: Any,
    skill_config: Any,
    bundle: Any,
    automation_repo: Any,
    fake_router: FakeRouter,
    config_dir: Path,
):
    """Construct DiscordIntentDispatcher + AutomationCreationPath + CadenceReclamper.

    CadenceReclamper is registered onto ``bundle.lifecycle_manager.after_state_change``
    so Wave-3 E2E tests that promote a skill see active_cadence_cron rewritten
    without any extra plumbing.
    """
    from donna.agents.challenger_agent import ChallengerAgent
    from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge
    from donna.automations.cadence_policy import CadencePolicy
    from donna.automations.cadence_reclamper import CadenceReclamper
    from donna.automations.creation_flow import AutomationCreationPath
    from donna.capabilities.matcher import CapabilityMatcher
    from donna.capabilities.registry import CapabilityRegistry
    from donna.integrations.discord_pending_drafts import PendingDraftRegistry
    from donna.orchestrator.discord_intent_dispatcher import (
        DiscordIntentDispatcher,
    )

    registry = CapabilityRegistry(db.connection, skill_config)
    matcher = CapabilityMatcher(registry, config=skill_config)

    challenger = ChallengerAgent(matcher=matcher, model_router=fake_router)
    novelty = ClaudeNoveltyJudge(model_router=fake_router, matcher=matcher)
    pending = PendingDraftRegistry()

    cadence_path = config_dir / "automations.yaml"
    policy = (
        CadencePolicy.load(cadence_path) if cadence_path.exists() else None
    )

    lifecycle_adapter = _SkillLifecycleStateAdapter(db.connection)
    tasks_adapter = _TasksDbAdapter(db)

    candidate_writer = (
        bundle.candidate_repo if bundle is not None else None
    )

    dispatcher = DiscordIntentDispatcher(
        challenger=challenger,
        novelty_judge=novelty,
        pending_drafts=pending,
        tasks_db=tasks_adapter,
        cadence_policy=policy,
        lifecycle_lookup=lifecycle_adapter,
        candidate_report_writer=candidate_writer,
    )

    creation_path = AutomationCreationPath(repository=automation_repo)

    reclamper: Any = None
    if policy is not None and bundle is not None:
        reclamper = CadenceReclamper(
            repo=automation_repo,
            policy=policy,
            scheduler=_SchedulerComputeNextRun(),
        )
        bundle.lifecycle_manager.after_state_change.register(
            lambda cap, new_state: reclamper.reclamp_for_capability(
                cap, new_state,
            ),
        )

    return dispatcher, creation_path, policy, reclamper
