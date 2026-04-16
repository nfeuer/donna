"""AutomationDispatcher — executes one due automation end-to-end.

Spec §6.9: skill vs claude_native resolution, per-run budget cap,
global BudgetGuard, alert evaluation + dispatch, consecutive-failure pause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import structlog

from donna.automations.models import AutomationRow
from donna.automations.repository import AutomationRepository
from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError
from donna.notifications.service import CHANNEL_TASKS

logger = structlog.get_logger()


@dataclass(slots=True)
class DispatchReport:
    automation_id: str
    run_id: str | None
    outcome: str
    alert_sent: bool
    error: str | None = None


class AutomationDispatcher:
    def __init__(
        self,
        *,
        connection: aiosqlite.Connection,
        repository: AutomationRepository,
        model_router: Any,
        skill_executor_factory: Callable[[], Any],
        budget_guard: Any,
        alert_evaluator: Any,
        cron: Any,
        notifier: Any,
        config: SkillSystemConfig,
    ) -> None:
        self._conn = connection
        self._repo = repository
        self._router = model_router
        self._skill_executor_factory = skill_executor_factory
        self._budget_guard = budget_guard
        self._alerts = alert_evaluator
        self._cron = cron
        self._notifier = notifier
        self._config = config

    async def dispatch(self, automation: AutomationRow) -> DispatchReport:
        now = datetime.now(timezone.utc)

        try:
            if self._budget_guard is not None:
                await self._budget_guard.check_pre_call(user_id=automation.user_id)
        except BudgetPausedError:
            next_run_at = self._compute_next_run(automation, now)
            await self._repo.advance_schedule(
                automation_id=automation.id,
                last_run_at=now, next_run_at=next_run_at,
                increment_run_count=False, increment_failure_count=False,
            )
            logger.info("automation_skipped_budget", automation_id=automation.id)
            return DispatchReport(
                automation_id=automation.id, run_id=None,
                outcome="skipped_budget", alert_sent=False,
            )

        path = await self._decide_path(automation.capability_name)
        run_id = await self._repo.insert_run(
            automation_id=automation.id, started_at=now,
            execution_path=path,
        )

        output: dict | None = None
        skill_run_id: str | None = None
        invocation_log_id: str | None = None
        cost_usd: float = 0.0
        run_status = "failed"
        error: str | None = None
        alert_sent = False
        alert_content: str | None = None

        try:
            if path == "skill":
                executor = self._skill_executor_factory()
                if executor is None:
                    raise RuntimeError("skill path selected but executor_factory returned None")
                result = await self._execute_skill(executor, automation)
                output = result.final_output if isinstance(result.final_output, dict) else None
                cost_usd = float(getattr(result, "total_cost_usd", 0.0) or 0.0)
                run_status = result.status
                if result.status != "succeeded":
                    error = getattr(result, "error", None) or getattr(result, "escalation_reason", None)
            else:
                parsed, metadata = await self._router.complete(
                    prompt=self._build_prompt(automation),
                    task_type=automation.capability_name,
                    task_id=None,
                    user_id=automation.user_id,
                )
                output = parsed if isinstance(parsed, dict) else {"output": parsed}
                invocation_log_id = getattr(metadata, "invocation_id", None)
                cost_usd = float(getattr(metadata, "cost_usd", 0.0) or 0.0)
                run_status = "succeeded"
        except BudgetPausedError:
            await self._repo.finish_run(
                run_id=run_id, status="skipped_budget",
                output=None, skill_run_id=None, invocation_log_id=None,
                alert_sent=False, alert_content=None, error=None,
                cost_usd=0.0,
            )
            next_run_at = self._compute_next_run(automation, now)
            await self._repo.advance_schedule(
                automation_id=automation.id, last_run_at=now,
                next_run_at=next_run_at,
                increment_run_count=False, increment_failure_count=False,
            )
            return DispatchReport(
                automation_id=automation.id, run_id=run_id,
                outcome="skipped_budget", alert_sent=False,
            )
        except Exception as exc:
            error = str(exc)
            run_status = "failed"
            logger.warning(
                "automation_run_exception",
                automation_id=automation.id, error=error,
            )

        if (
            run_status == "succeeded"
            and automation.max_cost_per_run_usd is not None
            and cost_usd > automation.max_cost_per_run_usd
        ):
            run_status = "failed"
            error = "cost_exceeded"

        if run_status == "succeeded" and output is not None:
            try:
                fires = self._alerts.evaluate(automation.alert_conditions, output)
            except Exception as exc:
                logger.warning(
                    "automation_alert_check_failed",
                    automation_id=automation.id, error=str(exc),
                )
                fires = False
            if fires:
                alert_content = self._render_alert_content(automation, output)
                try:
                    if self._notifier is not None:
                        await self._notifier.dispatch(
                            notification_type="automation_alert",
                            content=alert_content,
                            channel=CHANNEL_TASKS,
                            priority=3,
                        )
                        alert_sent = True
                except Exception:
                    logger.exception(
                        "automation_alert_dispatch_failed",
                        automation_id=automation.id,
                    )

        await self._repo.finish_run(
            run_id=run_id, status=run_status,
            output=output, skill_run_id=skill_run_id,
            invocation_log_id=invocation_log_id,
            alert_sent=alert_sent, alert_content=alert_content,
            error=error, cost_usd=cost_usd,
        )

        run_succeeded = run_status == "succeeded"
        next_run_at = self._compute_next_run(automation, now)
        await self._repo.advance_schedule(
            automation_id=automation.id, last_run_at=now,
            next_run_at=next_run_at,
            increment_run_count=True,
            increment_failure_count=not run_succeeded,
        )
        if run_succeeded:
            await self._repo.reset_failure_count(automation.id)

        if not run_succeeded:
            updated = await self._repo.get(automation.id)
            if (
                updated is not None
                and updated.failure_count >= self._config.automation_failure_pause_threshold
            ):
                await self._repo.set_status(automation.id, "paused")
                pause_msg = (
                    f"Automation '{automation.name}' paused after "
                    f"{updated.failure_count} consecutive failures. "
                    f"Last error: {error or 'unknown'}"
                )
                try:
                    if self._notifier is not None:
                        await self._notifier.dispatch(
                            notification_type="automation_failure",
                            content=pause_msg, channel=CHANNEL_TASKS, priority=4,
                        )
                except Exception:
                    logger.exception(
                        "automation_pause_notification_failed",
                        automation_id=automation.id,
                    )

        outcome = self._classify_outcome(run_status, error)
        return DispatchReport(
            automation_id=automation.id, run_id=run_id,
            outcome=outcome, alert_sent=alert_sent, error=error,
        )

    async def _decide_path(self, capability_name: str) -> str:
        cursor = await self._conn.execute(
            "SELECT state FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return "claude_native"
        state = row[0]
        if state in ("shadow_primary", "trusted"):
            return "skill"
        return "claude_native"

    async def _execute_skill(self, executor: Any, automation: AutomationRow) -> Any:
        cursor = await self._conn.execute(
            "SELECT id, capability_name, current_version_id, state, "
            "requires_human_gate, baseline_agreement, created_at, updated_at "
            "FROM skill WHERE capability_name = ?",
            (automation.capability_name,),
        )
        skill_row = await cursor.fetchone()
        if skill_row is None:
            raise RuntimeError("skill not found at dispatch time")
        cursor = await self._conn.execute(
            "SELECT id, skill_id, version_number, yaml_backbone, step_content, "
            "output_schemas, created_by, changelog, created_at "
            "FROM skill_version WHERE id = ?",
            (skill_row[2],),
        )
        version_row = await cursor.fetchone()
        if version_row is None:
            raise RuntimeError("skill version not found")
        from donna.skills.models import (
            row_to_skill,
            row_to_skill_version,
        )
        skill = row_to_skill(skill_row)
        version = row_to_skill_version(version_row)
        return await executor.execute(
            skill=skill, version=version,
            inputs=automation.inputs,
            user_id=automation.user_id,
        )

    def _compute_next_run(self, automation: AutomationRow, now: datetime) -> datetime | None:
        if automation.trigger_type != "on_schedule" or not automation.schedule:
            return None
        try:
            return self._cron.next_run(expression=automation.schedule, after=now)
        except Exception as exc:
            logger.warning(
                "automation_invalid_cron",
                automation_id=automation.id, error=str(exc),
            )
            return None

    def _build_prompt(self, automation: AutomationRow) -> str:
        return (
            f"Execute capability '{automation.capability_name}' with the following inputs. "
            f"Return a strict JSON object matching the capability's output schema.\n\n"
            f"Inputs:\n{json.dumps(automation.inputs, indent=2)}"
        )

    def _render_alert_content(self, automation: AutomationRow, output: dict) -> str:
        return (
            f"Automation '{automation.name}' alert:\n"
            f"Output: {json.dumps(output, indent=2)}"
        )

    @staticmethod
    def _classify_outcome(run_status: str, error: str | None) -> str:
        if run_status == "succeeded":
            return "succeeded"
        if error == "cost_exceeded":
            return "cost_exceeded"
        if run_status == "skipped_budget":
            return "skipped_budget"
        if run_status == "failed":
            return "failed" if error and error != "cost_exceeded" else "error"
        return "error"
