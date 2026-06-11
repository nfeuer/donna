from datetime import UTC, datetime, timedelta

import pytest

from donna.healthwatch.heartbeat_monitor import HeartbeatMonitor, is_stale


def test_is_stale_true_when_old():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(seconds=120)
    assert is_stale(now, old, threshold_seconds=90) is True


def test_is_stale_false_when_fresh():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    recent = now - timedelta(seconds=30)
    assert is_stale(now, recent, threshold_seconds=90) is False


def test_is_stale_true_when_missing():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    assert is_stale(now, None, threshold_seconds=90) is True


@pytest.mark.asyncio
async def test_monitor_alerts_once_on_stale_then_recovery():
    sent = []

    async def alert(message: str) -> None:
        sent.append(message)

    # heartbeat age returned by injected reader, in seconds: stale, stale, fresh
    ages = iter([200, 200, 5])

    def read_age() -> float | None:
        return next(ages)

    mon = HeartbeatMonitor(alert=alert, read_age_seconds=read_age, threshold_seconds=90)
    await mon.check_once()  # stale -> alert
    await mon.check_once()  # still stale -> no second alert
    await mon.check_once()  # fresh -> recovery alert
    assert len(sent) == 2
    assert "stale" in sent[0].lower()
    assert "resumed" in sent[1].lower() or "recover" in sent[1].lower()
