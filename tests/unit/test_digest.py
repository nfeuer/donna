"""Unit tests for MorningDigest.

Tests data assembly, LLM path, degraded fallback, and template rendering.
No real Discord, DB, calendar, or LLM connections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from donna.notifications.digest import MorningDigest, _next_fire_time, _render_template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hour: int, minute: int = 0, day: int = 20) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC)


def _make_task(
    task_id: str = "t1",
    title: str = "Task",
    status: str = "scheduled",
    deadline: str | None = None,
    scheduled_start: str | None = None,
    estimated_duration: int | None = 30,
    priority: int = 2,
) -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.status = status
    t.deadline = deadline
    t.scheduled_start = scheduled_start
    t.estimated_duration = estimated_duration
    t.priority = priority
    return t


def _make_digest(project_root: Path | None = None) -> tuple[MorningDigest, AsyncMock, AsyncMock, AsyncMock, MagicMock]:
    db = AsyncMock()
    service = AsyncMock()
    service.dispatch = AsyncMock(return_value=True)
    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"digest_text": "Good morning! Here is your day."}, MagicMock()))
    router._models_config = MagicMock()
    router._models_config.cost.monthly_budget_usd = 100.0

    calendar_client = AsyncMock()
    calendar_client.list_events = AsyncMock(return_value=[])

    conn_mock = AsyncMock()
    cursor_mock = AsyncMock()
    cursor_mock.fetchone = AsyncMock(return_value=(0.05,))
    conn_mock.execute = AsyncMock(return_value=cursor_mock)
    db.connection = conn_mock
    db.list_tasks = AsyncMock(return_value=[])

    root = project_root or Path("/tmp/test_donna")

    digest = MorningDigest(
        db=db,
        service=service,
        router=router,
        calendar_client=calendar_client,
        calendar_id="primary",
        user_id="u1",
        project_root=root,
    )
    return digest, db, service, router, calendar_client


# ---------------------------------------------------------------------------
# _next_fire_time helper
# ---------------------------------------------------------------------------


class TestNextFireTime:
    def test_next_fire_today_if_not_yet_reached(self) -> None:
        now = _utc(5, 0)  # 5 AM — before 6:30 AM
        result = _next_fire_time(now, 6, 30)
        assert result.hour == 6
        assert result.minute == 30
        assert result.date() == now.date()

    def test_next_fire_tomorrow_if_already_passed(self) -> None:
        now = _utc(7, 0)  # 7 AM — after 6:30 AM
        result = _next_fire_time(now, 6, 30)
        assert result.date() > now.date()
        assert result.hour == 6
        assert result.minute == 30

    def test_next_fire_tomorrow_if_exactly_now(self) -> None:
        now = _utc(6, 30)
        result = _next_fire_time(now, 6, 30)
        assert result.date() > now.date()


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestRenderTemplate:
    def test_jinja_style_substitution(self) -> None:
        template = "Hello {{ name }}! Today is {{ day_of_week }}."
        result = _render_template(template, {"name": "Nick", "day_of_week": "Friday"})
        assert "Nick" in result
        assert "Friday" in result
        assert "{{" not in result


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------


class TestAssembleData:
    async def test_tasks_due_today_included(self) -> None:
        digest, db, _service, _router, _cal = _make_digest()
        now = _utc(7, 0)  # 7 AM on 2026-03-20
        today_iso = "2026-03-20"

        task_due = _make_task(title="Submit report", deadline=today_iso, status="scheduled")
        db.list_tasks = AsyncMock(return_value=[task_due])

        data = await digest._assemble_data(now)
        assert "Submit report" in data["tasks_due_today"]

    async def test_done_tasks_excluded_from_overdue(self) -> None:
        digest, db, _service, _router, _cal = _make_digest()
        now = _utc(12, 0)
        # Task started yesterday 9 AM, duration 30 min → well overdue, but done
        start = datetime(2026, 3, 19, 9, 0, tzinfo=UTC)
        task_done = _make_task(
            title="Done task",
            status="done",
            scheduled_start=start.isoformat(),
            estimated_duration=30,
        )
        db.list_tasks = AsyncMock(return_value=[task_done])

        data = await digest._assemble_data(now)
        assert "Done task" not in data["overdue_tasks"]

    async def test_carryover_tasks_from_yesterday(self) -> None:
        digest, db, _service, _router, _cal = _make_digest()
        now = _utc(7, 0)  # 2026-03-20 7 AM
        yesterday_start = datetime(2026, 3, 19, 10, 0, tzinfo=UTC)
        task = _make_task(
            title="Pending carryover",
            status="scheduled",
            scheduled_start=yesterday_start.isoformat(),
        )
        db.list_tasks = AsyncMock(return_value=[task])

        data = await digest._assemble_data(now)
        assert "Pending carryover" in data["carryover_tasks"]

    async def test_cost_summary_present(self) -> None:
        digest, _db, _service, _router, _cal = _make_digest()
        now = _utc(7, 0)

        data = await digest._assemble_data(now)
        assert "yesterday_cost" in data
        assert "mtd_cost" in data
        assert "monthly_budget" in data


# ---------------------------------------------------------------------------
# _fire — LLM path
# ---------------------------------------------------------------------------


class TestFireLLMPath:
    async def test_llm_response_posted_as_embed(self, tmp_path: Path) -> None:
        # Create the prompt template file.
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "morning_digest.md").write_text(
            "Today: {{ current_date }}\n{{ calendar_events }}"
        )

        digest, _db, service, _router, _cal = _make_digest(project_root=tmp_path)

        now = _utc(6, 30)
        await digest._fire(now)

        service.dispatch.assert_called_once()
        kw = service.dispatch.call_args[1]
        assert kw["embed"] is not None
        assert "Good morning" in kw["content"]

    async def test_llm_failure_triggers_degraded_mode(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "morning_digest.md").write_text("Today: {{ current_date }}")

        digest, _db, service, router, _cal = _make_digest(project_root=tmp_path)
        router.complete = AsyncMock(side_effect=RuntimeError("API down"))

        now = _utc(6, 30)
        await digest._fire(now)

        service.dispatch.assert_called_once()
        kw = service.dispatch.call_args[1]
        # Degraded: no embed, plain text
        assert kw.get("embed") is None
        assert len(kw["content"]) > 0


# ---------------------------------------------------------------------------
# Degraded rendering
# ---------------------------------------------------------------------------


class TestDegradedRender:
    def test_degraded_render_includes_key_sections(self) -> None:
        digest, *_ = _make_digest()
        data = {
            "current_date": "2026-03-20",
            "day_of_week": "Friday",
            "calendar_events": "- Standup 9 AM",
            "tasks_due_today": "- Fix bug",
            "carryover_tasks": "None.",
            "overdue_tasks": "None.",
            "prep_work_results": "None.",
            "agent_activity": "None.",
            "system_status": "All good.",
            "yesterday_cost": "0.0500",
            "mtd_cost": "1.2300",
            "monthly_budget": "100.00",
        }
        text = digest._render_degraded(data)
        assert "Morning Digest" in text
        assert "Standup" in text
        assert "Fix bug" in text
        assert len(text) <= 2000
