"""Unit tests for email notification dispatch and Tier 3 escalation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from donna.config import CalendarConfig, SmsConfig
from donna.notifications.service import NotificationService

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_calendar_config() -> CalendarConfig:
    """Return a minimal CalendarConfig for testing."""
    from donna.config import (
        CalendarEntryConfig,
        CredentialsConfig,
        SchedulingConfig,
        SyncConfig,
        TimeWindowConfig,
        TimeWindowsConfig,
    )
    return CalendarConfig(
        calendars={"primary": CalendarEntryConfig(calendar_id="primary", access="read_write")},
        sync=SyncConfig(),
        scheduling=SchedulingConfig(),
        time_windows=TimeWindowsConfig(
            blackout=TimeWindowConfig(start_hour=0, end_hour=6),
            quiet_hours=TimeWindowConfig(start_hour=20, end_hour=24),
            work=TimeWindowConfig(start_hour=9, end_hour=17, days=[0, 1, 2, 3, 4]),
            personal=TimeWindowConfig(start_hour=9, end_hour=20, days=[0, 1, 2, 3, 4, 5, 6]),
            weekend=TimeWindowConfig(start_hour=9, end_hour=17, days=[5, 6]),
        ),
        credentials=CredentialsConfig(
            client_secrets_path="creds.json",
            token_path="token.json",
            scopes=["https://www.googleapis.com/auth/calendar"],
        ),
    )


def _make_service(gmail: MagicMock | None = None) -> NotificationService:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_embed = AsyncMock()
    return NotificationService(
        bot=bot,
        calendar_config=_make_calendar_config(),
        user_id="nick",
        gmail=gmail,
    )


# ------------------------------------------------------------------
# dispatch_email — blackout / quiet hours
# ------------------------------------------------------------------


class TestDispatchEmail:
    async def test_dispatch_email_blocked_during_blackout(self) -> None:
        """3 AM UTC is within blackout (0–6 AM) → returns False."""
        mock_gmail = AsyncMock()
        service = _make_service(gmail=mock_gmail)

        blackout_time = datetime(2026, 3, 20, 3, 0, tzinfo=UTC)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = blackout_time
            result = await service.dispatch_email(
                to="nick@example.com",
                subject="Test",
                body="Hello",
                priority=2,
            )

        assert result is False
        mock_gmail.create_draft.assert_not_called()

    async def test_dispatch_email_blocked_during_quiet_hours_low_priority(self) -> None:
        """9 PM UTC is quiet hours; priority 2 < 5 → blocked."""
        mock_gmail = AsyncMock()
        service = _make_service(gmail=mock_gmail)

        quiet_time = datetime(2026, 3, 20, 21, 0, tzinfo=UTC)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = quiet_time
            result = await service.dispatch_email(
                to="nick@example.com",
                subject="Test",
                body="Hello",
                priority=2,
            )

        assert result is False
        mock_gmail.create_draft.assert_not_called()

    async def test_dispatch_email_passes_quiet_hours_high_priority(self) -> None:
        """Priority 5 bypasses quiet hours → draft is created."""
        mock_gmail = AsyncMock()
        mock_gmail.create_draft.return_value = "draft-001"
        service = _make_service(gmail=mock_gmail)

        quiet_time = datetime(2026, 3, 20, 21, 0, tzinfo=UTC)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = quiet_time
            result = await service.dispatch_email(
                to="nick@example.com",
                subject="Urgent",
                body="Priority message",
                priority=5,
            )

        assert result is True
        mock_gmail.create_draft.assert_called_once_with(
            to="nick@example.com",
            subject="Urgent",
            body="Priority message",
        )

    async def test_dispatch_email_during_working_hours(self) -> None:
        """During normal hours (10 AM), email draft is created."""
        mock_gmail = AsyncMock()
        mock_gmail.create_draft.return_value = "draft-002"
        service = _make_service(gmail=mock_gmail)

        working_time = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = working_time
            result = await service.dispatch_email(
                to="nick@example.com",
                subject="Morning Digest",
                body="Here is your digest.",
                priority=5,
            )

        assert result is True
        mock_gmail.create_draft.assert_called_once()

    async def test_dispatch_email_no_client_returns_false(self) -> None:
        """Returns False and warns when no GmailClient is configured."""
        service = _make_service(gmail=None)

        working_time = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = working_time
            result = await service.dispatch_email(
                to="nick@example.com",
                subject="Test",
                body="Test",
            )

        assert result is False


# ------------------------------------------------------------------
# Escalation Tier 3
# ------------------------------------------------------------------


class TestEscalationTier3:
    async def test_escalation_tier3_sends_email_draft(self) -> None:
        """Advancing from Tier 2 to Tier 3 creates an email draft with 'ACTION REQUIRED'."""
        from donna.notifications.escalation import (
            STATUS_PENDING,
            EscalationManager,
            EscalationState,
        )

        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_db.connection = mock_conn

        mock_gmail = AsyncMock()
        mock_gmail.create_draft.return_value = "draft-tier3"

        mock_service = MagicMock()
        mock_service._is_blackout.return_value = False
        mock_service._is_quiet.return_value = False
        mock_service._gmail = mock_gmail
        mock_service.dispatch_email = AsyncMock(return_value=True)

        mock_sms = AsyncMock()
        sms_config = SmsConfig()

        manager = EscalationManager(
            db=mock_db,
            service=mock_service,
            sms=mock_sms,
            sms_config=sms_config,
            user_id="nick",
            user_phone="+15555555555",
            gmail=mock_gmail,
            user_email="nick@example.com",
        )

        now = datetime.now(tz=UTC)
        state = EscalationState(
            id="esc-001",
            user_id="nick",
            task_id="task-001",
            task_title="Finish the report",
            current_tier=2,
            status=STATUS_PENDING,
            next_escalation_at=now - timedelta(minutes=1),
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(minutes=1),
        )

        await manager._advance_one(state, now)

        # Tier 3 should send email escalation.
        mock_service.dispatch_email.assert_called_once()
        call_kwargs = mock_service.dispatch_email.call_args[1]
        assert "ACTION REQUIRED" in call_kwargs["subject"]
        assert "Finish the report" in call_kwargs["subject"]
        assert call_kwargs["priority"] == 5

    async def test_escalation_tier4_marks_completed(self) -> None:
        """Tier 4+ marks escalation as COMPLETED (phone TTS deferred)."""
        from donna.notifications.escalation import (
            STATUS_COMPLETED,
            STATUS_PENDING,
            EscalationManager,
            EscalationState,
        )

        mock_db = MagicMock()
        mock_conn = AsyncMock()
        mock_db.connection = mock_conn

        mock_service = MagicMock()
        mock_service.dispatch_email = AsyncMock(return_value=True)
        mock_sms = AsyncMock()
        sms_config = SmsConfig()

        manager = EscalationManager(
            db=mock_db,
            service=mock_service,
            sms=mock_sms,
            sms_config=sms_config,
            user_id="nick",
            user_phone="+15555555555",
        )

        now = datetime.now(tz=UTC)
        state = EscalationState(
            id="esc-002",
            user_id="nick",
            task_id="task-002",
            task_title="Write docs",
            current_tier=3,
            status=STATUS_PENDING,
            next_escalation_at=now - timedelta(minutes=1),
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(minutes=1),
        )

        await manager._advance_one(state, now)

        # State should be COMPLETED.
        update_call = mock_conn.execute.call_args_list[-1]
        sql = update_call[0][0]
        params = update_call[0][1]
        assert "UPDATE escalation_state" in sql
        assert STATUS_COMPLETED in params


# ------------------------------------------------------------------
# EodDigest weekday filtering
# ------------------------------------------------------------------


class TestEodDigestFireTime:
    def test_next_eod_skips_weekend(self) -> None:
        """next_eod_fire_time advances past Saturday and Sunday."""
        from donna.notifications.eod_digest import _next_eod_fire_time

        # Friday 2026-03-20 is a Friday (weekday=4), so if we fire at 18:00,
        # next should be Monday 2026-03-23.
        friday_after = datetime(2026, 3, 20, 18, 0, tzinfo=UTC)  # Past EOD on Friday.
        result = _next_eod_fire_time(friday_after, 17, 30, weekdays_only=True)

        # Result should be Monday.
        assert result.weekday() == 0  # Monday

    def test_next_eod_weekdays_only_false_fires_saturday(self) -> None:
        """When weekdays_only=False, fires on Saturday too."""
        from donna.notifications.eod_digest import _next_eod_fire_time

        friday_after = datetime(2026, 3, 20, 18, 0, tzinfo=UTC)
        result = _next_eod_fire_time(friday_after, 17, 30, weekdays_only=False)

        # Saturday is fine when weekdays_only=False.
        assert result.weekday() == 5  # Saturday

    def test_next_eod_fires_today_if_not_reached(self) -> None:
        """If we haven't hit EOD time today, fires today."""
        from donna.notifications.eod_digest import _next_eod_fire_time

        wednesday_morning = datetime(2026, 3, 18, 9, 0, tzinfo=UTC)  # Wednesday 9 AM
        result = _next_eod_fire_time(wednesday_morning, 17, 30, weekdays_only=True)

        # Should fire today (Wednesday).
        assert result.weekday() == 2  # Wednesday
        assert result.hour == 17
        assert result.minute == 30
