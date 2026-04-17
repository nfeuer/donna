from datetime import datetime, timezone

from donna.automations.models import (
    AutomationRow,
    AutomationRunRow,
    row_to_automation,
    row_to_automation_run,
)


def test_row_to_automation_parses_json_and_datetime():
    now = datetime.now(timezone.utc)
    row = (
        "a1", "nick", "Watch shirt", "Price monitor",
        "product_watch",
        '{"url": "https://cos.com/shirt"}',
        "on_schedule",
        "0 12 * * *",
        '{"all_of": [{"field": "price", "op": "<=", "value": 100}]}',
        '["discord"]',
        2.0, 300, "active",
        now.isoformat(), now.isoformat(),
        1, 0,
        now.isoformat(), now.isoformat(),
        "dashboard",
    )
    auto = row_to_automation(row)
    assert isinstance(auto, AutomationRow)
    assert auto.id == "a1"
    assert auto.inputs == {"url": "https://cos.com/shirt"}
    assert auto.alert_channels == ["discord"]
    assert auto.last_run_at == now
    assert auto.run_count == 1


def test_row_to_automation_run_parses_output():
    now = datetime.now(timezone.utc)
    row = (
        "r1", "a1", now.isoformat(), now.isoformat(),
        "succeeded", "skill", "sk1", None,
        '{"price_usd": 89}', 1, "alert body", None, 0.0,
    )
    run = row_to_automation_run(row)
    assert isinstance(run, AutomationRunRow)
    assert run.output == {"price_usd": 89}
    assert run.alert_sent is True


def test_row_to_automation_run_null_optional_fields():
    now = datetime.now(timezone.utc)
    row = (
        "r1", "a1", now.isoformat(), None,
        "skipped_budget", "claude_native", None, None,
        None, 0, None, None, None,
    )
    run = row_to_automation_run(row)
    assert run.finished_at is None
    assert run.output is None
    assert run.alert_sent is False
