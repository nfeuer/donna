from datetime import datetime, timezone

import pytest

from donna.automations.cron import (
    CronScheduleCalculator,
    InvalidCronExpressionError,
)


def test_next_run_daily_at_noon():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 16


def test_next_run_wraps_to_next_day():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 15, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 17


def test_next_run_every_5_minutes():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 12, 3, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="*/5 * * * *", after=ref)
    assert nxt.minute == 5
    assert nxt.hour == 12


def test_invalid_cron_expression_raises():
    calc = CronScheduleCalculator()
    with pytest.raises(InvalidCronExpressionError):
        calc.next_run(expression="not a cron", after=datetime.now(timezone.utc))


def test_result_is_timezone_aware_utc():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset().total_seconds() == 0
