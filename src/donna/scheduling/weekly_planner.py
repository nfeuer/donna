"""Weekly planning session — Phase 2.

Fires Monday mornings at a configurable time. Assembles the week's backlog
tasks (filtered by deadline proximity, priority, or staleness), resolves
dependency order, dry-runs slot assignments, and posts a plan to Discord
for user confirmation.

On user reply "confirm" → applies the plan (creates calendar events).
On user reply "skip [title]" → removes that task and re-posts.

Pending proposals are held in memory — an in-process dict is sufficient for
Phase 2 with a single user.

See docs/scheduling.md and docs/agents.md.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from donna.integrations.calendar import GoogleCalendarClient
from donna.notifications.service import NotificationService
from donna.scheduling.dependency_resolver import topological_sort
from donna.scheduling.priority_recalculator import PriorityRecalculator
from donna.scheduling.scheduler import Scheduler, ScheduledSlot
from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()

# Fire every Monday at this hour:minute UTC.
_DEFAULT_FIRE_HOUR = 8
_DEFAULT_FIRE_MINUTE = 0

# How long to wait for user confirmation before expiring the proposal.
_PROPOSAL_TTL_HOURS = 24

# Minimum priority to include a task in the weekly plan automatically.
_MIN_PRIORITY_AUTO = 3

# Include tasks that have been in backlog this many days without scheduling.
_STALE_BACKLOG_DAYS = 7


class WeeklyPlanner:
    """Assembles and proposes a weekly schedule every Monday morning.

    Usage:
        planner = WeeklyPlanner(db, scheduler, recalculator, service,
                                calendar_client, calendar_id, user_id)
        asyncio.create_task(planner.run())
    """

    def __init__(
        self,
        db: Database,
        scheduler: Scheduler,
        recalculator: PriorityRecalculator,
        service: NotificationService,
        calendar_client: GoogleCalendarClient,
        calendar_id: str,
        user_id: str,
        fire_hour: int = _DEFAULT_FIRE_HOUR,
        fire_minute: int = _DEFAULT_FIRE_MINUTE,
    ) -> None:
        self._db = db
        self._scheduler = scheduler
        self._recalculator = recalculator
        self._service = service
        self._calendar_client = calendar_client
        self._calendar_id = calendar_id
        self._user_id = user_id
        self._fire_hour = fire_hour
        self._fire_minute = fire_minute

        # In-memory pending proposals: {proposal_id: {"slots": [...], "tasks": [...]}}
        self._pending: dict[str, dict[str, Any]] = {}

    async def run(self) -> None:
        """Sleep until the next Monday at fire_hour:fire_minute, fire, repeat."""
        logger.info(
            "weekly_planner_started",
            fire_hour=self._fire_hour,
            fire_minute=self._fire_minute,
            user_id=self._user_id,
        )

        while True:
            now = datetime.now(tz=timezone.utc)
            next_fire = _next_monday_fire(now, self._fire_hour, self._fire_minute)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "weekly_planner_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire(datetime.now(tz=timezone.utc))
            except Exception:
                logger.exception("weekly_planner_fire_failed", user_id=self._user_id)

    async def _fire(self, now: datetime) -> None:
        """Assemble and post the weekly plan proposal."""
        # 1. Update priorities before planning.
        try:
            await self._recalculator.recalculate_and_apply(now)
        except Exception:
            logger.exception("weekly_planner_recalc_failed")

        # 2. Load and filter backlog candidates.
        all_tasks = await self._db.list_tasks(user_id=self._user_id)
        candidates = self._select_candidates(all_tasks, now)

        if not candidates:
            logger.info("weekly_planner_no_candidates", user_id=self._user_id)
            await self._service.dispatch(
                notification_type="weekly_plan",
                content="Weekly planning: no tasks need scheduling this week.",
                channel="tasks",
                priority=3,
            )
            return

        # 3. Sort topologically (blockers first).
        try:
            ordered = topological_sort(candidates)
        except Exception:
            logger.warning("weekly_planner_topo_sort_failed")
            ordered = candidates

        # 4. Dry-run slot assignments.
        cfg = self._scheduler._config.scheduling
        time_max = now + timedelta(days=cfg.search_horizon_days)
        try:
            existing_events = await self._calendar_client.list_events(
                self._calendar_id, now, time_max
            )
        except Exception:
            logger.exception("weekly_planner_list_events_failed")
            existing_events = []

        slot_ends: dict[str, datetime] = {}
        proposed_slots: list[tuple[TaskRow, ScheduledSlot]] = []

        from donna.scheduling.dependency_resolver import _parse_deps

        for task in ordered:
            dep_ids = _parse_deps(task.dependencies)
            after_dt = now
            for dep_id in dep_ids:
                if dep_id in slot_ends:
                    after_dt = max(after_dt, slot_ends[dep_id])

            try:
                slot = self._scheduler.find_next_slot(task, existing_events, now=after_dt)
                proposed_slots.append((task, slot))
                slot_ends[task.id] = slot.end
            except Exception:
                logger.warning("weekly_planner_no_slot", task_id=task.id, title=task.title)

        if not proposed_slots:
            await self._service.dispatch(
                notification_type="weekly_plan",
                content="Weekly planning: could not find slots for any candidates.",
                channel="tasks",
                priority=3,
            )
            return

        # 5. Store proposal and post to Discord.
        proposal_id = str(uuid.uuid4())
        self._pending[proposal_id] = {
            "tasks": [t for t, _ in proposed_slots],
            "slots": [s for _, s in proposed_slots],
            "expires_at": now + timedelta(hours=_PROPOSAL_TTL_HOURS),
        }

        message = self._format_proposal(proposal_id, proposed_slots, now)
        await self._service.dispatch(
            notification_type="weekly_plan",
            content=message,
            channel="tasks",
            priority=4,
        )

        logger.info(
            "weekly_plan_proposed",
            proposal_id=proposal_id,
            task_count=len(proposed_slots),
            user_id=self._user_id,
        )

    def _select_candidates(self, tasks: list[TaskRow], now: datetime) -> list[TaskRow]:
        """Filter backlog tasks that should be included in the weekly plan."""
        week_end = now + timedelta(days=7)
        stale_cutoff = now - timedelta(days=_STALE_BACKLOG_DAYS)

        candidates: list[TaskRow] = []
        for task in tasks:
            if task.status != "backlog":
                continue
            # Include if: has deadline this week
            if task.deadline:
                try:
                    dl = datetime.fromisoformat(task.deadline)
                    if dl.tzinfo is None:
                        dl = dl.replace(tzinfo=timezone.utc)
                    if dl <= week_end:
                        candidates.append(task)
                        continue
                except (ValueError, TypeError):
                    pass
            # Include if: high enough priority
            if task.priority >= _MIN_PRIORITY_AUTO:
                candidates.append(task)
                continue
            # Include if: been in backlog too long
            try:
                created = datetime.fromisoformat(task.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created <= stale_cutoff:
                    candidates.append(task)
            except (ValueError, TypeError):
                pass

        return candidates

    def _format_proposal(
        self,
        proposal_id: str,
        proposed_slots: list[tuple[TaskRow, ScheduledSlot]],
        now: datetime,
    ) -> str:
        """Format the weekly plan proposal message for Discord."""
        week_start = now.strftime("%b %d")
        week_end = (now + timedelta(days=6)).strftime("%b %d")
        lines = [
            f"**Weekly Plan — {week_start} to {week_end}**",
            f"_(proposal ID: `{proposal_id[:8]}`)_",
            "",
        ]
        for i, (task, slot) in enumerate(proposed_slots, start=1):
            day = slot.start.strftime("%a %b %d")
            time_range = f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')} UTC"
            lines.append(f"{i}. **{task.title}** — {day} {time_range} (priority {task.priority})")

        lines += [
            "",
            "Reply **confirm** to schedule all, or **skip [task title]** to remove a task.",
        ]
        return "\n".join(lines)

    async def handle_plan_reply(
        self, message_text: str, now: datetime | None = None
    ) -> bool:
        """Handle a user reply to a weekly plan proposal.

        Returns True if the reply was handled (matched a pending proposal),
        False if no pending proposal exists.
        """
        now = now or datetime.now(tz=timezone.utc)

        # Expire old proposals.
        expired = [pid for pid, p in self._pending.items() if p["expires_at"] < now]
        for pid in expired:
            del self._pending[pid]

        if not self._pending:
            return False

        # Use the most recent pending proposal.
        proposal_id = max(self._pending)
        proposal = self._pending[proposal_id]
        text = message_text.strip().lower()

        if text == "confirm":
            await self._apply_proposal(proposal_id)
            del self._pending[proposal_id]
            return True

        if text.startswith("skip "):
            skip_title = message_text.strip()[5:].strip().strip("'\"")
            tasks = proposal["tasks"]
            slots = proposal["slots"]
            new_pairs = [
                (t, s) for t, s in zip(tasks, slots)
                if skip_title.lower() not in t.title.lower()
            ]
            proposal["tasks"] = [t for t, _ in new_pairs]
            proposal["slots"] = [s for _, s in new_pairs]

            # Re-post updated proposal.
            msg = self._format_proposal(
                proposal_id, new_pairs, now
            )
            await self._service.dispatch(
                notification_type="weekly_plan",
                content=f"Updated plan (skipped '{skip_title}'):\n{msg}",
                channel="tasks",
                priority=4,
            )
            return True

        return False

    async def _apply_proposal(self, proposal_id: str) -> None:
        """Apply a confirmed proposal: create calendar events for all tasks."""
        proposal = self._pending.get(proposal_id)
        if not proposal:
            return

        tasks: list[TaskRow] = proposal["tasks"]
        slots: list[ScheduledSlot] = proposal["slots"]

        for task, slot in zip(tasks, slots):
            try:
                event = await self._calendar_client.create_event(
                    calendar_id=self._calendar_id,
                    summary=task.title,
                    start=slot.start,
                    end=slot.end,
                    task_id=task.id,
                )
                from donna.tasks.db_models import TaskStatus
                if task.status == TaskStatus.BACKLOG.value:
                    await self._db.transition_task_state(task.id, TaskStatus.SCHEDULED)
                await self._db.update_task(
                    task.id,
                    scheduled_start=slot.start,
                    calendar_event_id=event.event_id,
                    donna_managed=True,
                )
                logger.info(
                    "weekly_plan_task_scheduled",
                    task_id=task.id,
                    slot_start=slot.start.isoformat(),
                )
            except Exception:
                logger.exception("weekly_plan_apply_failed", task_id=task.id)

        task_count = len(tasks)
        await self._service.dispatch(
            notification_type="weekly_plan",
            content=f"Weekly plan confirmed — {task_count} task(s) scheduled.",
            channel="tasks",
            priority=4,
        )


def _next_monday_fire(now: datetime, hour: int, minute: int) -> datetime:
    """Return the next Monday at hour:minute UTC, at least 1 second away."""
    # Days until next Monday (weekday 0).
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        # Today is Monday — check if fire time is still ahead.
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
        days_until_monday = 7

    target = now + timedelta(days=days_until_monday)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)
