from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from donna.automations.cron import (
    CronScheduleCalculator,
    InvalidCronExpressionError,
)


def test_next_run_daily_at_noon():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 16


def test_next_run_wraps_to_next_day():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 15, 0, tzinfo=UTC)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 17


def test_next_run_every_5_minutes():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 12, 3, tzinfo=UTC)
    nxt = calc.next_run(expression="*/5 * * * *", after=ref)
    assert nxt.minute == 5
    assert nxt.hour == 12


def test_invalid_cron_expression_raises():
    calc = CronScheduleCalculator()
    with pytest.raises(InvalidCronExpressionError):
        calc.next_run(expression="not a cron", after=datetime.now(UTC))


def test_result_is_timezone_aware_utc():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset().total_seconds() == 0


def test_next_run_interprets_cron_in_configured_tz_summer():
    # During EDT (UTC-4), "9 AM Eastern" is 13:00 UTC.
    calc = CronScheduleCalculator(tz=ZoneInfo("America/New_York"))
    ref = datetime(2026, 6, 10, 6, 0, tzinfo=UTC)  # 02:00 EDT
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 6, 10, 13, 0, tzinfo=UTC)


def test_next_run_interprets_cron_in_configured_tz_winter():
    # During EST (UTC-5), "9 AM Eastern" is 14:00 UTC (DST correctness).
    calc = CronScheduleCalculator(tz=ZoneInfo("America/New_York"))
    ref = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)  # 01:00 EST
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 1, 10, 14, 0, tzinfo=UTC)


def test_next_run_defaults_to_utc_when_no_tz():
    # No tz => legacy UTC interpretation (backward compatible).
    calc = CronScheduleCalculator()
    ref = datetime(2026, 6, 10, 6, 0, tzinfo=UTC)
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
