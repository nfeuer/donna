from datetime import datetime, timezone

import pytest

from donna.capabilities.models import CapabilityRow, row_to_capability


def test_capability_row_basic():
    row = CapabilityRow(
        id="abc",
        name="product_watch",
        description="desc",
        input_schema={"type": "object"},
        trigger_type="on_schedule",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        created_by="seed",
        notes=None,
    )
    assert row.name == "product_watch"
    assert row.status == "active"


def test_row_to_capability_parses_json_fields():
    raw = (
        "abc",
        "product_watch",
        "desc",
        '{"type": "object"}',
        "on_schedule",
        None,
        "active",
        None,
        "2026-04-15T00:00:00+00:00",
        "seed",
        None,
    )
    cap = row_to_capability(raw)
    assert cap.input_schema == {"type": "object"}
    assert cap.trigger_type == "on_schedule"
    assert cap.created_at.year == 2026
